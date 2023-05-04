import json
from typing import List, Literal
from nox.utils.registry import register_object, get_object
from nox.datasets.abstract import AbstractDataset
from nox.datasets.brenda import Brenda, BrendaReaction
from nox.utils.messages import METAFILE_NOTFOUND_ERR
from tqdm import tqdm
import argparse
import hashlib
from rich import print as rprint
import pickle
from rxn.chemutils.smiles_randomization import randomize_smiles_rotated
import warnings
from frozendict import frozendict
import copy, os
import numpy as np
from p_tqdm import p_map
import random
from collections import defaultdict
import rdkit 
import torch

@register_object("ecreact", "dataset")
class ECReact(BrendaReaction):
    def __init__(self, args, split_group) -> None:
        super(ECReact, ECReact).__init__(self, args, split_group)
        self.metadata_json = None  # overwrite for memory

    def load_dataset(self, args: argparse.ArgumentParser) -> None:
        """Loads dataset file

        Args:
            args (argparse.ArgumentParser)

        Raises:
            Exception: Unable to load
        """
        try:
            self.metadata_json = json.load(open(args.dataset_file_path, "r"))
        except Exception as e:
            raise Exception(METAFILE_NOTFOUND_ERR.format(args.dataset_file_path, e))

        # self.ec2uniprot = pickle.load(open("/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_ec2uniprot.p", "rb"))
        self.uniprot2sequence = pickle.load(
            open(
                "/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_proteins.p", "rb"
            )
        )
        self.uniprot2cluster =  pickle.load(open('/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_mmseq_clusters.p', 'rb'))

    def create_dataset(
        self, split_group: Literal["train", "dev", "test"]
    ) -> List[dict]:

        dataset = []

        mcsa_data = self.load_mcsa_data(self.args)
        for reaction in tqdm(self.metadata_json):

            ec = reaction["ec"]
            reactants = reaction["reactants"]
            products = reaction["products"]
            reaction_string = ".".join(reactants) + ">>" + ".".join(products)

            uniprotid = reaction["uniprot_id"]
            sample_id = hashlib.md5(
                f"{uniprotid}_{reaction_string}".encode()
            ).hexdigest()
            sequence = self.uniprot2sequence[uniprotid]
            residues = self.get_uniprot_residues(mcsa_data, sequence, ec)

            sample = {
                "protein_id": uniprotid,
                "sequence": sequence,
                "reactants": reactants,
                "products": products,
                "ec": ec,
                "reaction_string": reaction_string,
                "sample_id": sample_id,
                "split": reaction.get("split", None),
            }

            sample.update(
                {
                    "residues": residues["residues"],
                    "residue_mask": residues["residue_mask"],
                    "has_residues": residues["has_residues"],
                    "residue_positions": residues["residue_positions"],
                }
            )

            if self.skip_sample(sample, split_group):
                continue

            if self.args.split_type != "random":
                del sample["sequence"]
            # add sample to dataset
            dataset.append(sample)

        return dataset

    def skip_sample(self, sample, split_group) -> bool:
        # if sequence is unknown
        if (sample["sequence"] is None) or (len(sample["sequence"]) == 0):
            return True

        if (self.args.max_protein_length is not None) and len(
            sample["sequence"]
        ) > self.args.max_protein_length:
            return True

        return False

    def get_split_group_dataset(
        self, processed_dataset, split_group: Literal["train", "dev", "test"]
    ) -> List[dict]:
        # check right split
        dataset = []
        for sample in processed_dataset:
            if hasattr(self, "to_split"):
                if self.args.split_type == "sequence":
                    if self.to_split[sample["protein_id"]] != split_group:
                        continue

                if self.args.split_type == "mmseqs":
                    cluster = self.uniprot2cluster[sample["protein_id"]]
                    if self.to_split[cluster] != split_group:
                        continue 

                if self.args.split_type == "ec":
                    ec = ".".join(sample["ec"].split(".")[: self.args.ec_level + 1])
                    if self.to_split[ec] != split_group:
                        continue

                if self.args.split_type == "product":
                    if any(self.to_split[p] != split_group for p in sample["products"]):
                        continue

            elif sample["split"] is not None:
                if sample["split"] != split_group:
                    continue

            dataset.append(sample)
        return dataset

    def get_pesto_scores(self, uniprot):
        filepath = f"{self.args.pesto_scores_directory}/AF-{uniprot}-F1-model_v4.pt"
        if not os.path.exists(filepath):
            return None
        scores_dict = torch.load(filepath)
        chain = "A:0"  # * NOTE: hardcoded because currently only option
        residue_ids = scores_dict[chain]["resid"]
        residue_ids_unique = np.unique(residue_ids, return_index=True)[1]
        scores = scores_dict[chain]["ligand"][residue_ids_unique]
        return torch.tensor(scores)

    @staticmethod
    def set_args(args) -> None:
        super(ECReact, ECReact).set_args(args)
        args.dataset_file_path = (
            "/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_dataset_lite_v2.json"
        )

    def __getitem__(self, index):
        sample = self.dataset[index]

        try:

            reactants, products = copy.deepcopy(sample["reactants"]), copy.deepcopy(
                sample["products"]
            )

            # incorporate sequence residues if known
            if self.args.use_residues_in_reaction:
                residues = sample["residues"]
                reactants.extend(residues)
                products.extend(residues)

            reaction = "{}>>{}".format(".".join(reactants), ".".join(products))
            # randomize order of reactants and products
            if self.args.randomize_order_in_reaction:
                np.random.shuffle(reactants)
                np.random.shuffle(products)
                reaction = "{}>>{}".format(".".join(reactants), ".".join(products))

            if self.args.use_random_smiles_representation:
                try:
                    reactants = [randomize_smiles_rotated(s) for s in reactants]
                    products = [randomize_smiles_rotated(s) for s in products]
                    reaction = "{}>>{}".format(".".join(reactants), ".".join(products))
                except:
                    pass

            item = {
                "reaction": reaction,
                "reactants": ".".join(reactants),
                "products": ".".join(products),
                "sequence": sequence,
                "ec": sample["ec"],
                "organism": sample.get("organism", "none"),
                "protein_id": uniprot_id,
                "sample_id": sample["sample_id"],
                "residues": ".".join(sample["residues"]),
                "has_residues": sample["has_residues"],
                "residue_positions": ".".join(
                    [str(s) for s in sample["residue_positions"]]
                ),
            }

            if self.args.precomputed_esm_features_dir is not None:
                esm_features = pickle.load(
                    open(
                        os.path.join(
                            self.args.precomputed_esm_features_dir,
                            f"sample_{sample['protein_id']}.predictions",
                        ),
                        "rb",
                    )
                )

                mask_hiddens = esm_features["mask_hiddens"]  # sequence len, 1
                protein_hidden = esm_features["hidden"]
                token_hiddens = esm_features["token_hiddens"][mask_hiddens[:, 0].bool()]
                item.update(
                    {
                        # "token_hiddens": token_hiddens,
                        "protein_len": mask_hiddens.sum(),
                        "hidden": protein_hidden,
                    }
                )

            return item

        except Exception:
            warnings.warn(f"Could not load sample: {sample['sample_id']}")


@register_object("ecreact_proteins", "dataset")
class ECReactProteins(AbstractDataset):
    def load_dataset(self, args: argparse.ArgumentParser) -> None:
        """Loads dataset file

        Args:
            args (argparse.ArgumentParser)

        Raises:
            Exception: Unable to load
        """
        self.metadata_json = pickle.load(open(args.dataset_file_path, "rb"))

    @staticmethod
    def set_args(args) -> None:
        super(ECReactProteins, ECReactProteins).set_args(args)
        args.dataset_file_path = (
            "/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_proteins.p"
        )

    def create_dataset(
        self, split_group: Literal["train", "dev", "test"]
    ) -> List[dict]:
        dataset = []
        for uniprot_id, sequence in tqdm(self.metadata_json.items()):
            if self.skip_sample(sequence):
                continue
            dataset.append({"sample_id": uniprot_id, "sequence": sequence})

        return dataset

    def __getitem__(self, index):
        return self.dataset[index]

    def skip_sample(self, sequence):
        if sequence is None:
            return True

        if len(sequence) > self.args.max_protein_length:
            return True

        return False

    @staticmethod
    def add_args(parser) -> None:
        """Add class specific args

        Args:
            parser (argparse.ArgumentParser): argument parser
        """
        super(ECReactProteins, ECReactProteins).add_args(parser)
        parser.add_argument(
            "--max_protein_length",
            type=int,
            default=None,
            help="skip proteins longer than max_protein_length",
        )


@register_object("ecreact_reactions", "dataset")
class ECReact_RXNS(ECReact):
    def init_class(self, args: argparse.ArgumentParser, split_group: str) -> None:
        """Perform Class-Specific init methods
           Default is to load JSON dataset

        Args:
            args (argparse.ArgumentParser)
            split_group (str)
        """
        self.load_dataset(args)

        self.ec2uniprot = pickle.load(
            open(
                "/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_ec2uniprot.p",
                "rb",
            )
        )
        self.valid_ec2uniprot = {}
        self.uniprot2sequence = pickle.load(
            open(
                "/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_proteins.p", "rb"
            )
        )
        self.uniprot2cluster =  pickle.load(open('/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_mmseq_clusters.p', 'rb'))
        # self.mcsa_data = self.load_mcsa_data(self.args)

    def assign_splits(self, metadata_json, split_probs, seed=0) -> None:
        """
        Assigns each sample to a split group based on split_probs
        """
        # get all samples
        self.to_split = {}

        # set seed
        np.random.seed(seed)

        # assign groups
        if self.args.split_type in ["mmseqs", "sequence", "ec", "product"]:
            
            if self.args.split_type == "mmseqs":
                samples = list(self.uniprot2cluster.values())

            if self.args.split_type == "sequence":
                # split based on uniprot_id
                samples = [
                    u
                    for reaction in metadata_json
                    for u in self.ec2uniprot.get(reaction["ec"], [])
                ]

            elif self.args.split_type == "ec":
                # split based on ec number
                samples = [reaction["ec"] for reaction in metadata_json]

                # option to change level of ec categorization based on which to split
                samples = [
                    ".".join(e.split(".")[: self.args.ec_level + 1]) for e in samples
                ]

            elif self.args.split_type == "product":
                # split by reaction product (splits share no products)
                if any(len(s["products"]) > 1 for s in metadata_json):
                    raise NotImplementedError(
                        "Product split not implemented for multi-products"
                    )

                samples = [p for s in metadata_json for p in s["products"]]

            samples = sorted(list(set(samples)))
            np.random.shuffle(samples)
            split_indices = np.ceil(
                np.cumsum(np.array(split_probs) * len(samples))
            ).astype(int)
            split_indices = np.concatenate([[0], split_indices])

            for i in range(len(split_indices) - 1):
                self.to_split.update(
                    {
                        sample: ["train", "dev", "test"][i]
                        for sample in samples[split_indices[i] : split_indices[i + 1]]
                    }
                )

        elif self.args.split_type == "recoverable_mapping_product":
            products = defaultdict(list)
            for s in metadata_json:
                products[s["products"][0]].append(
                    int(s.get("mapped_recoverable_reaction", None) is not None)
                )

            recoverable_products = {
                p: sum(v) for p, v in products.items() if sum(v) == len(v)
            }

            # shuffle products
            product_names = sorted(list(recoverable_products.keys()))
            np.random.shuffle(product_names)

            # get products necessary to achieve X %
            # manually set to get ~ 5% reactions into test
            num_products = (
                np.cumsum([recoverable_products[p] for p in product_names])
                < split_probs[2] * len(metadata_json)
            ).sum() - 1
            test_products = product_names[:num_products]
            self.to_split.update({p: "test" for p in test_products})

            # add dev products
            train_dev_products = [p for p in products if p not in self.to_split]
            num_dev_products = (
                np.cumsum([len(products[p]) for p in train_dev_products])
                < split_probs[1] * len(metadata_json)
            ).sum() - 1
            dev_products = train_dev_products[:num_dev_products]
            self.to_split.update({p: "dev" for p in dev_products})

            # add train products
            train_products = [p for p in products if p not in self.to_split]
            self.to_split.update({p: "train" for p in train_products})

        # random splitting
        elif self.args.split_type == "random":
            for sample in self.metadata_json:
                reaction_string = (
                    ".".join(sample["reactants"]) + ">>" + ".".join(sample["products"])
                )
                self.to_split.update(
                    {
                        reaction_string: np.random.choice(
                            ["train", "dev", "test"], p=split_probs
                        )
                    }
                )
        else:
            raise ValueError("Split type not supported")

    def create_dataset(
        self, split_group: Literal["train", "dev", "test"]
    ) -> List[dict]:
        self.mol2size = {}
        self.uniprot2substrates = defaultdict(set)
        dataset = []

        for rowid, reaction in tqdm(
            enumerate(self.metadata_json),
            desc="Building dataset",
            total=len(self.metadata_json),
            ncols=100,
        ):

            ec = reaction["ec"]
            reactants = sorted(reaction["reactants"])
            products = reaction["products"]
            reaction_string = ".".join(reactants) + ">>" + ".".join(products)

            valid_uniprots = []
            for uniprot in self.ec2uniprot.get(ec, []):
                temp_sample = {
                    "reactants": reactants,
                    "products": products,
                    "ec": ec,
                    "reaction_string": reaction_string,
                    "protein_id": uniprot,
                    "sequence": self.uniprot2sequence[uniprot],
                    "split": reaction["split"],
                    "mapped_reaction": reaction.get("mapped_reaction", None),
                }
                if self.skip_sample(temp_sample, split_group):
                    continue
                
                self.uniprot2substrates[uniprot].update(products)

                valid_uniprots.append(uniprot)

            if len(valid_uniprots) == 0:
                continue

            if ec not in self.valid_ec2uniprot:
                self.valid_ec2uniprot[ec] = valid_uniprots

            sample = {
                "reactants": reactants,
                "products": products,
                "ec": ec,
                "split": reaction["split"],
                "reaction_string": reaction_string,
                "rowid": rowid,
                "mapped_reaction": reaction.get("mapped_reaction", None),
                "mapped_recoverable_reaction": reaction.get(
                    "mapped_recoverable_reaction", None
                ),
                "bond_changes": reaction.get("bond_changes", None),
                "mapped_reactants": reaction.get("mapped_reactants", None),
                "mapped_products": reaction.get("mapped_products", None),
            }

            if self.args.atom_map_reactions:
                sample["mapped_reaction"] = get_atom_mapped_reaction(
                    reaction_string, self.args
                )
                if sample["mapped_reaction"] is None:
                    continue

            # add reaction sample to dataset
            dataset.append(sample)

        return dataset

    def skip_sample(self, sample, split_group) -> bool:
        if '-' in sample['ec']:
            return True 
            
        # if sequence is unknown
        sequence = sample["sequence"]
        if (sequence is None) or (len(sequence) == 0):
            return True

        if (self.args.max_protein_length is not None) and len(
            sequence
        ) > self.args.max_protein_length:
            return True

        if sample["mapped_reaction"] is None:
            True

        
        if self.args.max_reactant_size is not None:
            for mol in sample["reactants"]:
                if not (mol in self.mol2size):
                    self.mol2size[mol] = rdkit.Chem.MolFromSmiles(mol).GetNumAtoms()
                if self.mol2size[mol] > self.args.max_reactant_size:
                    return True 

        if self.args.max_product_size is not None:
            for mol in sample["products"]:
                if not (mol in self.mol2size):
                    self.mol2size[mol] = rdkit.Chem.MolFromSmiles(mol).GetNumAtoms()
                if self.mol2size[mol] > self.args.max_product_size:
                    return True 

        return False

    def get_split_group_dataset(
        self, processed_dataset, split_group: Literal["train", "dev", "test"]
    ) -> List[dict]:
        dataset = []
        for sample in processed_dataset:
            # check right split
            if self.args.split_type == "ec":
                ec = sample["ec"]
                split_ec = ".".join(ec.split(".")[: self.args.ec_level + 1])
                if self.to_split[split_ec] != split_group:
                    continue

            elif self.args.split_type == "mmseqs":
                cluster = self.uniprot2cluster[sample["protein_id"]]
                if self.to_split[cluster] != split_group:
                    continue 
                    

            elif self.args.split_type in ["product", "recoverable_mapping_product"]:
                products = sample["products"]
                if any(self.to_split[p] != split_group for p in products):
                    continue

            elif self.args.split_type == "sequence":
                uniprot = sample["protein_id"]
                if self.to_split[uniprot] != split_group:
                    continue

            elif sample["split"] is not None:
                if sample["split"] != split_group:
                    continue
            dataset.append(sample)
        return dataset

    def post_process(self, args):
        # add all possible products
        reaction_to_products = defaultdict(set)
        for sample in self.dataset:
            reaction_to_products[f"{sample['ec']}{'.'.join(sample['reactants'])}"].update(sample['products'])
        self.reaction_to_products = reaction_to_products

    @staticmethod
    def set_args(args):
        args.dataset_file_path = "/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_mapped_ibm_splits.json"

    @staticmethod
    def add_args(parser) -> None:
        """Add class specific args

        Args:
            parser (argparse.ArgumentParser): argument parser
        """
        super(ECReact_RXNS, ECReact_RXNS).add_args(parser)
        parser.add_argument(
            "--add_active_residues_to_item",
            action="store_true",
            default=False,
            help="whether to add active site residues to getitem sample if available",
        )
        parser.add_argument(
            "--use_pesto_scores",
            action="store_true",
            default=False,
            help="use pesto scores",
        )
        parser.add_argument(
            "--pesto_scores_directory",
            type=str,
            default="/Mounts/rbg-storage1/datasets/Enzymes/ECReact/pesto_ligands",
            help="load pesto scores from directory predictions",
        )

    def __getitem__(self, index):
        sample = self.dataset[index]

        try:
            reactants, products = copy.deepcopy(sample["reactants"]), copy.deepcopy(
                sample["products"]
            )

            ec = sample["ec"]
            valid_uniprots = self.valid_ec2uniprot[ec]
            uniprot_id = random.sample(valid_uniprots, 1)[0]
            sequence = self.uniprot2sequence[uniprot_id]

            if self.args.add_active_residues_to_item:
                residue_dict = self.get_uniprot_residues(self.mcsa_data, sequence, ec)
                residues = residue_dict["residues"]
                residue_mask = residue_dict["residue_mask"]
                has_residues = residue_dict["has_residues"]
                residue_positions = residue_dict["residue_positions"]

            # incorporate sequence residues if known
            if self.args.use_residues_in_reaction:
                reactants.extend(residues)
                # products.extend(residues)

            reaction = "{}>>{}".format(".".join(reactants), ".".join(products))
            # randomize order of reactants and products
            if self.args.randomize_order_in_reaction:
                np.random.shuffle(reactants)
                np.random.shuffle(products)
                reaction = "{}>>{}".format(".".join(reactants), ".".join(products))

            if self.args.use_random_smiles_representation:
                try:
                    reactants = [randomize_smiles_rotated(s) for s in reactants]
                    products = [randomize_smiles_rotated(s) for s in products]
                    reaction = "{}>>{}".format(".".join(reactants), ".".join(products))
                except:
                    pass

            # sample_id = hashlib.md5(f"{uniprot_id}_{sample['reaction_string']}".encode()).hexdigest()
            sample_id = f"{sample['ec']}_{uniprot_id}"
            item = {
                "reaction": reaction,
                "reactants": ".".join(reactants),
                "products": ".".join(products),
                "sequence": sequence,
                "ec": ec,
                "organism": sample.get("organism", "none"),
                "protein_id": uniprot_id,
                "sample_id": sample_id,
                "all_smiles": list(self.reaction_to_products[f"{ec}{'.'.join(sorted(reactants))}"]),
                "smiles": ".".join(products),
            }

            if sample.get("source", False):
                sample["all_smiles"] = products
            elif "decoder" in self.args.model_name:
                sample["all_smiles"] = self.uniprot2substrates[uniprot_id]

            if self.args.add_active_residues_to_item:
                item.update(
                    {
                        "residues": ".".join(residues),
                        "has_residues": has_residues,
                        "residue_positions": ".".join(
                            [str(s) for s in residue_positions]
                        ),
                    }
                )

            if self.args.precomputed_esm_features_dir is not None:
                esm_features = pickle.load(
                    open(
                        os.path.join(
                            self.args.precomputed_esm_features_dir,
                            f"sample_{uniprot_id}.predictions",
                        ),
                        "rb",
                    )
                )

                mask_hiddens = esm_features["mask_hiddens"]  # sequence len, 1
                protein_hidden = esm_features["hidden"]
                token_hiddens = esm_features["token_hiddens"][mask_hiddens[:, 0].bool()]
                item.update(
                    {
                        # "token_hiddens": token_hiddens,
                        "protein_len": mask_hiddens.sum(),
                        "hidden": protein_hidden,
                    }
                )
            
            if self.args.use_pesto_scores:
                scores = self.get_pesto_scores(item["protein_id"])
                if (scores is None) or (scores.shape[0] != len(item["sequence"])):
                    # make all zeros of length sequence
                    scores = torch.zeros(len(item["sequence"]))
                item["sequence_annotation"] = scores

            return item

        except Exception:
            warnings.warn(f"Could not load sample: {sample['sample_id']}")

    @property
    def SUMMARY_STATEMENT(self) -> None:
        reactions = [
            "{}>>{}".format(".".join(d["reactants"]), ".".join(d["products"]))
            for d in self.dataset
        ]
        proteins = [u for d in self.dataset for u in self.valid_ec2uniprot[d["ec"]]]
        ecs = [d["ec"] for d in self.dataset]
        statement = f""" 
        * Number of reactions: {len(set(reactions))}
        * Number of proteins: {len(set(proteins))}
        * Number of ECs: {len(set(ecs))}
        """
        return statement


@register_object("ecreact_reactions_full", "dataset")
class ECReactRxnsFull(ECReact_RXNS):
   
    def create_dataset(
        self, split_group: Literal["train", "dev", "test"]
    ) -> List[dict]:

        dataset = []

        for rowid, reaction in tqdm(
            enumerate(self.metadata_json),
            desc="Building dataset",
            total=len(self.metadata_json),
            ncols=100,
        ):
            self.mol2size = {}

            ec = reaction["ec"]
            reactants = sorted(reaction["reactants"])
            products = reaction["products"]
            reaction_string = ".".join(reactants) + ">>" + ".".join(products)

            valid_uniprots = []
            for uniprot in self.ec2uniprot.get(ec, []):
                temp_sample = {
                    "reactants": reactants,
                    "products": products,
                    "ec": ec,
                    "reaction_string": reaction_string,
                    "protein_id": uniprot,
                    "sequence": self.uniprot2sequence[uniprot],
                    "split": reaction["split"],
                    "mapped_reaction": reaction.get("mapped_reaction", None),
                }
                if self.skip_sample(temp_sample, split_group):
                    continue

                valid_uniprots.append(uniprot)

            if len(valid_uniprots) == 0:
                continue

            for uniprot in valid_uniprots:

                sample = {
                    "reactants": reactants,
                    "products": products,
                    "ec": ec,
                    "split": reaction["split"],
                    "reaction_string": reaction_string,
                    "rowid": f"{uniprot}_{rowid}",
                    "uniprot_id": uniprot,
                    "protein_id": uniprot,
                }
                # add reaction sample to dataset
                dataset.append(sample)

        return dataset

    def __getitem__(self, index):
        sample = self.dataset[index]

        try:
            reactants, products = copy.deepcopy(sample["reactants"]), copy.deepcopy(
                sample["products"]
            )

            ec = sample["ec"]
            uniprot_id = sample['uniprot_id']
            sequence = self.uniprot2sequence[uniprot_id]

            reaction = "{}>>{}".format(".".join(reactants), ".".join(products))
            # randomize order of reactants and products
            if self.args.randomize_order_in_reaction:
                np.random.shuffle(reactants)
                np.random.shuffle(products)
                reaction = "{}>>{}".format(".".join(reactants), ".".join(products))

            if self.args.use_random_smiles_representation:
                try:
                    reactants = [randomize_smiles_rotated(s) for s in reactants]
                    products = [randomize_smiles_rotated(s) for s in products]
                    reaction = "{}>>{}".format(".".join(reactants), ".".join(products))
                except:
                    pass

            # sample_id = hashlib.md5(f"{uniprot_id}_{sample['reaction_string']}".encode()).hexdigest()
            sample_id = sample["rowid"]
            item = {
                "reaction": reaction,
                "reactants": ".".join(reactants),
                "products": ".".join(products),
                "sequence": sequence,
                "ec": ec,
                "organism": sample.get("organism", "none"),
                "protein_id": uniprot_id,
                "sample_id": sample_id,
                "smiles": ".".join(products),
                "all_smiles": list(self.reaction_to_products[f"{ec}{'.'.join(sorted(reactants))}"]),
            }

            if self.args.use_pesto_scores:
                scores = self.get_pesto_scores(item["protein_id"])
                if (scores is None) or (scores.shape[0] != len(item["sequence"])):
                    # make all zeros of length sequence
                    scores = torch.zeros(len(item["sequence"]))
                item["sequence_annotation"] = scores

            return item

        except Exception:
            warnings.warn(f"Could not load sample: {sample['sample_id']}")

    @property
    def SUMMARY_STATEMENT(self) -> None:
        reactions = [
            "{}>>{}".format(".".join(d["reactants"]), ".".join(d["products"]))
            for d in self.dataset
        ]
        proteins = [d["uniprot_id"] for d in self.dataset]
        ecs = [d["ec"] for d in self.dataset]
        statement = f""" 
        * Number of reactions: {len(set(reactions))}
        * Number of proteins: {len(set(proteins))}
        * Number of ECs: {len(set(ecs))}
        """
        return statement

@register_object("ecreact_multiproduct_reactions", "dataset")
class ECReact_MultiProduct_RXNS(ECReact_RXNS):
    @staticmethod
    def set_args(args):
        args.dataset_file_path = (
            "/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_multiproduct.json"
        )

    def skip_sample(self, sample, split_group) -> bool:
        if super().skip_sample(sample, split_group):
            return True

        if len(sample["reaction_string"]) > 2000:
            return True

        return False


@register_object("ecreact+orgos", "dataset")
class EC_Orgo_React(ECReact_RXNS):
    def init_class(self, args, split_group) -> None:
        super(EC_Orgo_React, EC_Orgo_React).init_class(self, args, split_group)
        if split_group == 'train':
            orgo_args = copy.deepcopy(args)
            orgo_args.dataset_file_path = "/Mounts/rbg-storage1/datasets/ChemicalReactions/uspto_synthesis_dataset.json"
            orgo_args.assign_splits = False 
            orgo_args.class_bal = False 
            orgo_reactions = []
            for split in ["train", "dev", "test"]:
                orgo_reactions += get_object("chemical_reactions", "dataset")(orgo_args, split).dataset
            
            self.valid_ec2uniprot["z.z.z.z"] = []
            for sample in orgo_reactions:
                sampleid = sample["sample_id"]
                reaction = sample["x"].split(">>")
                sample.update({
                    "reactants": reaction[0].split('.'),
                    "products": reaction[-1].split('.'),
                    "split": "train",
                    "protein_id": "spontaneous",
                    "rowid": sampleid,
                    "ec": "z.z.z.z",
                    "sequence": "<pad>", # esm pad token
                    "source": "uspto",
                    "all_smiles": reaction[-1].split('.')
                })
            self.valid_ec2uniprot["z.z.z.z"]= ["spontaneous"]
            self.uniprot2sequence["spontaneous"] = "<pad>"
            self.orgo_reactions = orgo_reactions
        
            
    def get_split_group_dataset(self, processed_dataset, split_group):
        dataset = super(EC_Orgo_React, EC_Orgo_React).get_split_group_dataset(self, processed_dataset, split_group)
        for d in dataset: d["source"] = "ec"
        if split_group == "train":
            dataset = dataset + self.orgo_reactions
        
        return dataset