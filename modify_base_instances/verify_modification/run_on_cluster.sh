#!/bin/bash

#SBATCH --job-name=mthesis_verify_mods
#output paths have to adjusted to your folders
#SBATCH --output=/scratch/htc/lmehl/master_thesis/instances_scripts/modify_base_instances/verify_modification/job_out/sbatch_job_%A.out
#SBATCH --error=/scratch/htc/lmehl/master_thesis/instances_scripts/modify_base_instances/verify_modification/job_out/sbatch_job_%A.err
#SBATCH --partition=big
#SBATCH --time=10:00:00
#SBATCH --mem=10G
#SBATCH --cpus-per-task=4
#SBATCH --exclude=htc-gpu[001-115]

#for usage at z1 cluster set http and https proxy environment variables
export https_proxy=http://squid.zib.de:3128
export http_proxy=http://squid.zib.de:3128 

source ~/conda_init

python /scratch/htc/lmehl/master_thesis/instances_scripts/modify_base_instances/verify_modification/compare_lp_numeric_values.py \
    /scratch/opt/lmehl/instances/base/2050-100-el_eff-heatstrgflowcapcostsremoved-strgflowvarcostsremoved-othersmallvarcostsremoved-1e9boundssettoinfty.lp \
    /scratch/opt/lmehl/instances/base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp  \
    --lookahead 500000 \
    --show 150