import json
import argparse
from tqdm import tqdm
from p_tqdm import p_map
import requests
import pandas as pd


UNIPROT_QUERY_URL = "https://rest.uniprot.org/uniprotkb/search?query=reviewed:true+AND+ec:{}&format=json&fields=id,sequence,cc_alternative_products&size=500"
UNIPROT_ENTRY_URL = "https://rest.uniprot.org/uniprotkb/{}.fasta"

parser = argparse.ArgumentParser(
    description="Make EC React Dataset (https://github.com/rxn4chemistry/biocatalysis-model)"
)
parser.add_argument(
    "--react_csv_path",
    type=str,
    default="/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact-1.0.csv",
    help="Path to EC React entries file",
)
parser.add_argument(
    "-o",
    "--output_file_path",
    default="/Mounts/rbg-storage1/datasets/Enzymes/ECReact/ecreact_dataset.json",
    help="Path to output file",
)


def parse_fasta(f):
    """Parse fasta data

    Args:
        f (str): fasta data

    Returns:
        str: protein sequence
    """
    _seq = ""
    for _line in f.split("\n"):
        if _line.startswith(">"):
            continue
        _seq += _line.strip()
    return _seq


def get_protein_fasta(uniprot):
    """Get protein info from uniprot

    Args:
        uniprot (str): uniprot
    """

    fasta = requests.get(UNIPROT_ENTRY_URL.format(uniprot))

    if fasta.status_code == 200: # Success
        sequence = parse_fasta(fasta.text)
        return sequence 

    return 


if __name__ == "__main__":
    args = parser.parse_args()

    react_dataset = pd.read_csv(args.react_csv_path)
    # for i, row in tqdm(react_dataset.iterrows(), total=len(react_dataset)):
    def parse_react_row(row):
        dataset = []
        iso_dataset = []
        uni2seq = {}
        rxn_smiles = row["rxn_smiles"]
        ec = row["ec"]
        db_source = row["source"]

        full_reactants, products = rxn_smiles.split(">>")
        products = products.split(".")
        reactants_str, ec_str = full_reactants.split("|")
        reactants = reactants_str.split(".")
        # get the proteins sequences; while loop
        num_uniprot_results = 500
        while num_uniprot_results >= 500:
            uniprot_results = requests.get(UNIPROT_QUERY_URL.format(ec))
            if uniprot_results.status_code == 200:
                uniprot_results = uniprot_results.json()["results"]
                num_uniprot_results = len(uniprot_results)
                for uniprot_result in uniprot_results:
                    uniprot_id = uniprot_result["primaryAccession"]
                    sequence = uniprot_result["sequence"]["value"]
                    uni2seq[uniprot_id] = sequence
                    
                    for comment in uniprot_result["comments"]:
                        for isoform in comment.get("isoforms", []):
                            isoform_uniprots = isoform["isoformIds"]
                            for isoform_u in isoform_uniprots:
                                iso_dataset.append(isoform_u)
                                   
            else:
                num_uniprot_results = 0

        
        for uni,seq in uni2seq.items():
            dataset.append(
                        {
                            "reactants": reactants,
                            "products": products,
                            "sequence": seq,
                            "ec": ec,
                            "uniprot_id": uni,
                            "db_source": db_source
                        }
                    )
        
        iso_dataset = list(set(iso_dataset))
        iso_dataset = [u for u in iso_dataset if u not in uni2seq]
        iso_dataset = [{
                        "reactants": reactants,
                        "products": products,
                        "ec": ec,
                        "uniprot_id": uni,
                        "db_source": db_source
        } for uni in iso_dataset]

        return (dataset, iso_dataset)

    # transform csv to json
    react_dataset_rows = react_dataset.to_dict('records')
    reactions_dataset = []
    for row in react_dataset_rows:
        rxn_smiles = row["rxn_smiles"]
        ec = row["ec"]
        db_source = row["source"]

        full_reactants, products = rxn_smiles.split(">>")
        products = products.split(".")
        reactants_str, ec_str = full_reactants.split("|")
        reactants = reactants_str.split(".")
        reactions_dataset.append(
                        {
                            "reactants": reactants,
                            "products": products,
                            "ec": ec,
                            "db_source": db_source
                        }
                    )
    json.dump(reactions_dataset, open(args.react_csv_path.replace(".csv", ".json"), "w"))


    # match ec to uniprots, sequences, and isoforms
    reference_dataset = p_map(parse_react_row, react_dataset_rows)
    dataset, iso_dataset = [], []
    for d in reference_dataset:
        dataset.extend(d[0])
        iso_dataset.extend(d[1])
    
    # pass through isoforms
    isoform_uniprots = [d['uniprot_id'] for d in iso_dataset]
    isform_sequences = p_map(get_protein_fasta, isoform_uniprots)
    for i, sample in enumerate(iso_dataset):
        sample["sequence"] = isform_sequences[i]
        dataset.append(sample)

    json.dump(dataset, open(args.output_file_path, "w"))
