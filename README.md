[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/pgmikhael/CLIPZyme/blob/main/LICENSE.txt) 
[![arXiv](https://img.shields.io/badge/arXiv-1234.56789-b31b1b.svg)](https://arxiv.org/abs/2402.06748)
<!-- ![version](https://img.shields.io/badge/version-1.0.2-success) -->

# CLIPZyme

Implementation of the paper [**CLIPZyme: Reaction-Conditioned Virtual Screening of Enzymes**](https://github.com/pgmikhael/CLIPZyme/blob/main/LICENSE.txt)



Table of contents
=================

<!--ts-->
- [CLIPZyme](#clipzyme)
- [Table of contents](#table-of-contents)
- [Installation:](#installation)
- [Checkpoints and Data Files:](#checkpoints-and-data-files)
- [Screening with CLIPZyme](#screening-with-clipzyme)
  - [Using CLIPZyme's screening set](#using-clipzymes-screening-set)
  - [Using your own screening set](#using-your-own-screening-set)
    - [Interactive (slow)](#interactive-slow)
    - [Batched (fast)](#batched-fast)
- [Reproducing published results](#reproducing-published-results)
  - [Data processing](#data-processing)
  - [Training and evaluation](#training-and-evaluation)
  - [Downloading Batched AlphaFold Database Structures](#downloading-batched-alphafold-database-structures)
  - [Citation](#citation)
    
<!--te-->

# Installation:

1. Clone the repository:
```bash
git clone https://github.com/pgmikhael/CLIPZyme.git
```
2. Install the dependencies:
```bash
cd clipzyme
conda env create -f environment.yml
conda activate clipzyme
python -m pip install clipzyme
```

3. Download ESM-2 checkpoint `esm2_t33_650M_UR50D`. The `esm_dir` argument should point to this directory. The following command will download the checkpoint directly:
```bash
wget https://dl.fbaipublicfiles.com/fair-esm/models/esm2_t33_650M_UR50D.pt
```

# Checkpoints and Data Files:

The model checkpoint and data are available on Zenodo [here](https://zenodo.org/records/11187895):

- [clipzyme_data.zip](https://zenodo.org/records/11187895/files/clipzyme_data.zip?download=1):
  - The following commands will download the checkpoint directly: 
  ```
  wget https://zenodo.org/records/11187895/files/clipzyme_data.zip
  unzip clipzyme_data.zip -d files
  ```
  - Note that the data files should be extracted into the `files/` directory.
      - `enzymemap.json`: contains the EnzymeMap dataset.
      - `cached_enzymemap.p`: contains the processed EnzymeMap dataset.
      - `clipzyme_screening_set.p`: contains the screening set as dict of UniProt IDs and precomputed protein embeddings.
      - `uniprot2sequence.p`: contains the mapping form sequence ID to amino acids.

- [clipzyme_model.zip](https://zenodo.org/records/11187895/files/clipzyme_model.zip?download=1):
  - The following command will download the checkpoint directly: 
  ```
  wget https://zenodo.org/records/11187895/files/clipzyme_model.zip
  unzip clipzyme_model.zip -d files
  ```
    - `clipzyme_model.ckpt`: the trained model checkpoint.



# Screening with CLIPZyme

## Using CLIPZyme's screening set

First, download the screening set and extract the files into `files/`.


```python
import pickle
from clipzyme import CLIPZyme

## Load the screening set
##-----------------------
screenset = pickle.load(open("files/clipzyme_screening_set.p", 'rb'))
screen_hiddens = screenset["hiddens"] # hidden representations (261907, 1280)
screen_unis = screenset["uniprots"] # uniprot ids (261907,)

## Load the model and obtain the hidden representations of a reaction
##-------------------------------------------------------------------
model = CLIPZyme(checkpoint_path="files/clipzyme_model.ckpt")
reaction = "[CH3:1][N+:2]([CH3:3])([CH3:4])[CH2:5][CH:6]=[O:7].[O:9]=[O:10].[OH2:8]>>[CH3:1][N+:2]([CH3:3])([CH3:4])[CH2:5][C:6](=[O:7])[OH:8].[OH:9][OH:10]"
reaction_embedding = model.extract_reaction_features(reaction=reaction) # (1,1280)

enzyme_scores = screen_hiddens @ reaction_embedding.T # (261907, 1)

```

## Using your own screening set

Prepare your data as a CSV in the following format, and save it as `files/new_data.csv`. For the cases where we wish only to obtain the hidden representations of the sequences, the `reaction` column can be left empty (and vice versa).

| reaction                                                                                                                                         | sequence                                                                                                                                                                               | protein_id | cif      |
| ------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | -------- |
| [CH3:1][N+:2]([CH3:3])([CH3:4])[CH2:5][CH:6]=[O:7].[O:9]=[O:10].[OH2:8]>>[CH3:1][N+:2]([CH3:3])([CH3:4])[CH2:5][C:6](=[O:7])[OH:8].[OH:9][OH:10] | MGLSDGEWQLVLNVWGKVEAD<br>IPGHGQEVLIRLFKGHPETLE<br>KFDKFKHLKSEDEMKASEDLK<br>KHGATVLTALGGILKKKGHHE<br>AELKPLAQSHATKHKIPIKYL<br>EFISEAIIHVLHSRHPGDFGA<br>DAQGAMNKALELFRKDIAAKY<br>KELGYQG | P69905     | 1a0s.cif |


### Interactive (slow)
    
```python
from torch.utils.data import DataLoader
from clipzyme import CLIPZyme
from clipzyme import ReactionDataset
from clipzyme.utils.loading import ignore_None_collate

## Create reaction dataset
#-------------------------
reaction_dataset = DataLoader(
  ReactionDataset(
    dataset_file_path = "files/new_data.csv",
    esm_dir = "/path/to/esm2_dir",
    protein_cache_dir = "/path/to/protein_cache", # optional, where to cache processed protein graphs
  ),
  batch_size=1,
  collate_fn=ignore_None_collate,
)


## Load the model
#----------------
model = CLIPZyme(checkpoint_path="files/clipzyme_model.ckpt")
model = model.eval() # optional 

## For reaction-enzyme pair
#--------------------------
for batch in reaction_dataset:
  output = model(batch) 
  enzyme_scores = output.scores
  protein_hiddens = output.protein_hiddens
  reaction_hiddens = output.reaction_hiddens

## For sequences only
#--------------------
for batch in reaction_dataset:
  protein_hiddens = model.extract_protein_features(batch) 
  
## For reactions only
#--------------------
for batch in reaction_dataset:
  reaction_hiddens = model.extract_reaction_features(batch)

```

### Batched (fast)

1. Update the screening config `configs/screening.json` with the path to your data and indicate what you want to save and where:


```JSON
{
  "dataset_file_path": ["files/new_data.csv"],
  "inference_dir": ["/where/to/save/embeddings_and_scores"],
  "save_hiddens": [true], # whether to save the hidden representations
  "save_predictions": [true], # whether to save the reaction-enzyme pair scores
  "use_as_protein_encoder": [true], # whether to use the model as a protein encoder only
  "use_as_reaction_encoder": [true], # whether to use the model as a reaction encoder only
  "esm_dir": ["/data/esm/checkpoints"], path to ESM-2 checkpoints
  "gpus": [8], # number of gpus to use,
  "protein_cache_dir": ["/path/to/protein_cache"], # where to save the protein cache [optional]
  ...
}
```

If you want to use specific GPUs, you can specify them in the `available_gpus` field. For example, to use GPUs 0, 1, and 2, set `available_gpus` to `["0,1,2"]`.



2. Run the dispatcher with the screening config:

```bash
python scripts/dispatcher.py -c configs/screening.json -l ./logs/
```

3. Load the saved embeddings and scores:

```python
from clipzyme import collect_screening_results

screen_hiddens, screen_unis, enzyme_scores = collect_screening_results("configs/screening.json")

```


---------------------

# Reproducing published results

## Data processing

We obtain the data from the following sources:
- [EnzymeMap:](`https://doi.org/10.5281/zenodo.7841780`) Heid et al. Enzymemap: Curation, validation and data-driven prediction of enzymatic reactions. 2023.
- [Terpene Synthases:](`https://zenodo.org/records/10567437`) Samusevich et al. Discovery and characterization of terpene synthases powered by machine learning. 2024. 

Our processed data is can be downloaded from [here](https://zenodo.org/records/11187895). 


## Training and evaluation
1. To train the models presented in the tables below, run the following command:
    ```
    python scripts/dispatcher.py -c {config_path} -l {log_path}
    ```
    - `{config_path}` is the path to the config file in the table below 
    - `{log_path}` is the path in which to save the log file. 
    
    For example, to run the first row in Table 1, run:
    ```
    python scripts/dispatcher.py -c configs/train/clip_egnn.json -l ./logs/
    ```
2. Once you've trained the model, run the eval config to evaluate the model on the test set. For example, to evaluate the first row in Table 1, run:
    ```
    python scripts/dispatcher.py -c configs/eval/clip_egnn.json -l ./logs/
    ```
3. We perform all analysis in the jupyter notebook included [Results.ipynb](analysis/Results.ipynb). We first calculate the hidden representations of the screening using the eval configs above and collect them into one matrix (saved as a pickle file). These are loaded into the jupyter notebook as well as the test set. All tables are then generated in the notebook.

## Downloading Batched AlphaFold Database Structures

Assuming you have a list of uniprot IDs (called `uniprot_ids`) you can run the following to create a .txt file with the Google Storage urls for the AF2 structures:
```
file_paths = [f"gs://public-datasets-deepmind-alphafold-v4/AF-{u}-F1-model_v4.cif" for u in uniprot_ids]
output_file = 'uniprot_cif_paths.txt' 
open(output_file, 'w') as file:
    file.write('\n'.join(file_paths))
```
Then install gsutils (https://cloud.google.com/storage/docs/gsutil_install) and run the following command:
```
cat uniprot_cif_paths.txt | gsutil -m cp -I /path/to/output/dir/
```


## Citation

```bibtex
@article{mikhael2024clipzyme,
  title={CLIPZyme: Reaction-Conditioned Virtual Screening of Enzymes},
  author={Mikhael, Peter G and Chinn, Itamar and Barzilay, Regina},
  journal={arXiv preprint arXiv:2402.06748},
  year={2024}
}
```