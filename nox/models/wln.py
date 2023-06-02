import torch 
import torch.nn as nn
import torch.nn.functional as F
from nox.utils.registry import get_object, register_object
from nox.utils.classes import set_nox_type
from nox.utils.pyg import unbatch
from nox.utils.wln_processing import generate_candidates_from_scores
from nox.models.abstract import AbstractModel
from torch_scatter import scatter, scatter_add
from torch_geometric.utils import to_dense_batch, to_dense_adj
from nox.models.gat import GAT


@register_object("cheap_global_attn", "model")
class CheapGlobalAttention(AbstractModel):
    def __init__(self, args):
        super(CheapGlobalAttention, self).__init__()
        self.linear = nn.Linear(args.gat_hidden_dim, 1)
        
    def forward(self, node_feats, batch_index):
        # node_feats is (N, in_dim)
        scores = self.linear(node_feats)  # (N, 1)
        scores = torch.softmax(scores, dim=0)  # softmax over all nodes
        scores = scores.squeeze(1)  # (N, )
        out = scatter_add(node_feats * scores.unsqueeze(-1), batch_index, dim=0)
        return out

@register_object("pairwise_global_attn", "model")
class PairwiseAttention(AbstractModel):
    def __init__(self, args):
        super(PairwiseAttention, self).__init__()
        self.query_transform = nn.Linear(args.gat_hidden_dim, args.gat_hidden_dim)
        self.key_transform = nn.Linear(args.gat_hidden_dim, args.gat_hidden_dim)

    def forward(self, node_feats, batch_index):
        # Node features: N x F, where N is number of nodes, F is feature dimension
        # Batch index: N, mapping each node to corresponding graph index

        # Compute attention scores
        queries = self.query_transform(node_feats)  # N x F
        keys = self.key_transform(node_feats)  # N x F
        scores = torch.matmul(queries, keys.transpose(-2, -1))  # N x N

        # Mask attention scores to prevent attention to nodes in different graphs
        mask = batch_index[:, None] != batch_index[None, :]  # N x N
        scores = scores.masked_fill(mask, float('-inf'))

        # Compute attention weights
        weights = torch.sigmoid(scores)  # N x N

        # Apply attention weights
        weighted_feats = torch.matmul(weights, node_feats)  # N x F

        return weighted_feats


@register_object("gatv2_globalattn", "model")
class GATWithGlobalAttn(GAT):
    def __init__(self, args):
        super().__init__(args)
        self.global_attention = get_object(args.attn_type, "model")(args)

    def forward(self, graph):
        output = super().forward(graph) # Graph NN (GAT)

        weighted_node_feats = self.global_attention(output["node_features"], graph.batch)  # EQN 6

        output["node_features_attn"] = weighted_node_feats
        return output

    @staticmethod
    def add_args(parser) -> None:
        """Add class specific args

        Args:
            parser (argparse.ArgumentParser): argument parser
        """
        super(GATWithGlobalAttn, GATWithGlobalAttn).add_args(parser)
        parser.add_argument(
            "--attn_type",
            type=str,
            action=set_nox_type("model"),
            default="pairwise_global_attn",
            help="type of global attention to use"
        )


@register_object("reaction_center_net", "model")
class ReactivityCenterNet(AbstractModel):
    def __init__(self, args):
        super().__init__()
        self.args = args
        self.gat_global_attention = GATWithGlobalAttn(args)
        self.M_a = nn.Linear(args.gat_hidden_dim, args.gat_hidden_dim)
        self.M_b = nn.Linear(args.gat_edge_dim, args.gat_hidden_dim)
        self.U = nn.Sequential(
            nn.Linear(args.gat_hidden_dim, args.gat_hidden_dim),
            nn.ReLU(),
            nn.Linear(args.gat_hidden_dim, args.num_predicted_bond_types) # TODO: Change to predict bond type 
        )

    def forward(self, batch):
        gat_output = self.gat_global_attention(batch['reactants']) # GAT + Global Attention over node features
        cs = gat_output["node_features"]
        c_tildes = gat_output["node_features_attn"]

        s_uv = self.forward_helper(cs, batch['reactants']['edge_index'], batch['reactants']['edge_attr'], batch['reactants']['batch'])
        s_uv_tildes = self.forward_helper(c_tildes, batch['reactants']['edge_index'], batch['reactants']['edge_attr'], batch['reactants']['batch'])

        return {
            "cs": cs,
            "c_tildes": c_tildes,
            "s_uv": s_uv,
            "s_uv_tildes": s_uv_tildes
        }

    def forward_helper(self, node_features, edge_indices, edge_attr, batch_indices):
        # GAT with global attention
        node_features = self.M_a(node_features) # N x hidden_dim -> N x hidden_dim 
        edge_attr = self.M_b(edge_attr.float()) # E x 3 -> E x hidden_dim 

        # convert to dense adj: E x hidden_dim -> N x N x hidden_dim
        dense_edge_attr = to_dense_adj(edge_index = edge_indices, edge_attr = edge_attr).squeeze(0)

        # node_features: sparse batch: N x D
        pairwise_node_feats = node_features.unsqueeze(1) + node_features # N x N x D
        # edge_attr: bond features: N x N x D
        s = self.U(dense_edge_attr + pairwise_node_feats).squeeze(-1) # N x N
        # removed this line since the sizes become inconsistent later
        # s, mask = to_dense_batch(s, batch_indices) # B x max_batch_N x N x num_predicted_bond_types
        return s

    @staticmethod
    def add_args(parser) -> None:
        """Add class specific args

        Args:
            parser (argparse.ArgumentParser): argument parser
        """
        super(ReactivityCenterNet, ReactivityCenterNet).add_args(parser)
        parser.add_argument(
            "--num_predicted_bond_types",
            type=int,
            default=5,
            help="number of bond types to predict, this is t in the paper"
        )

@register_object("wldn", "model")
class WLDN(GATWithGlobalAttn):
    def __init__(self, args):
        super().__init__(args)
        self.args = args
        self.reactivity_net = get_object(args.reactivity_net_type, "model")(args)
        try:
            state_dict = torch.load(args.reactivity_model_path)
            self.reactivity_net.load_state_dict({k[len("model.gat_global_attention."):]: v for k,v in state_dict.items() if k.startswith("model")})
        except:
            print("Could not load pretrained model")
        self.wln_diff = GAT(args) # WLN for difference graph
        self.final_transform = nn.Linear(args.gat_hidden_dim, 1) # for scoring
        
    def forward(self, batch):
        with torch.no_grad():
            reactivity_output = self.reactivity_net(batch)
            reactant_node_feats = reactivity_output["cs"]

        # get candidate products as graph structures
        # each element in this list is a batch of candidate products (where each batch represents one sample)
        if self.training:
            mode = "train"
        else:
            mode = "test"
        
        product_candidates_list = generate_candidates_from_scores(reactivity_output, batch, self.args, mode)
        
        # TODO: CHECK append true product to product_candidates_list
        # TODO: atom-mapping: node i in reactants = node i in products
        candidate_scores = []
        for idx, product_candidates in enumerate(product_candidates_list):
            # get node features for candidate products
            with torch.no_grad():
                candidate_output = self.reactivity_net(product_candidates)
                candidate_node_feats = candidate_output["cs"]

            # compute difference vectors and replace the node features of the product graph with them
            difference_vectors = candidate_node_feats - reactant_node_feats[idx] # TODO: check this idx is ok
            product_candidates.x = difference_vectors

            # apply a separate WLN to the difference graph
            wln_diff_output = self.wln_diff(product_candidates)
            diff_node_feats = wln_diff_output["node_features"]

            # compute the score for each candidate product
            # to dense
            diff_node_feats = to_dense_batch(diff_node_feats, product_candidates['reactants']['batch'])[idx]
            score = self.final_transform(torch.sum(diff_node_feats, dim=-2))
            candidate_scores.append(score) # K x 1

        candidates_scores = torch.cat(candidate_scores, dim=0) # B x K x 1
        return candidate_scores

    @staticmethod
    def add_args(parser) -> None:
        """Add class specific args

        Args:
            parser (argparse.ArgumentParser): argument parser
        """
        super(WLDN, WLDN).add_args(parser)
        parser.add_argument(
            "--num_candidate_bond_changes",
            type=int,
            default=20,
            help="Core size"
        )
        parser.add_argument(
            "--max_num_bond_changes",
            type=int,
            default=5,
            help="Combinations"
        )
        parser.add_argument(
            "--max_num_change_combos_per_reaction",
            type=int,
            default=500,
            help="cutoff"
        )
        parser.add_argument(
            "--reactivity_net_type",
            type=str,
            action=set_nox_type("model"),
            default="reaction_center_net",
            help="Type of reactivity net to use, mainly to init args"
        )