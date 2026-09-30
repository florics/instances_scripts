#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# User settings
# -----------------------------------------------------------------------------

PYTHON_SCRIPT="/scratch/htc/lmehl/master_thesis/instances_scripts/create_scenario_instances/multiply_demands.py"
OUTPUT_DIR="/scratch/opt/lmehl/instances/scenario_catalogue/"
#OUTPUT_DIR="/scratch/opt/lmehl/instances/scenario_catalogue/debug"

# Plain .lp, .lp.gz, and single-file .zip inputs are supported.
INSTANCES=(
    "/scratch/opt/lmehl/instances/base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp.gz"
    "/scratch/opt/lmehl/instances/base/2050-100-el_eff-nothingmodified.lp.gz"
    #"/scratch/opt/lmehl/instances/debug_instances/oemofB3_debug_3costmods_boundsremoved_investvarsmerged.lp"
)

# One global demand multiplier is applied per run.
FACTORS=(
    "1.06"
    "1.07"
    "1.08"
    "1.09"
    "1.1"
    "1.15"
    "1.2"
)

# -----------------------------------------------------------------------------
# Run experiments
# -----------------------------------------------------------------------------

mkdir -p "$OUTPUT_DIR"

for input_file in "${INSTANCES[@]}"; do
    if [[ ! -f "$input_file" ]]; then
        echo "ERROR: input file does not exist: $input_file" >&2
        exit 1
    fi

    filename=$(basename "$input_file")

    # Remove compression suffix first. ZIP input is converted to gzip output.
    case "$filename" in
        *.gz)  uncompressed_name="${filename%.gz}" ;;
        *.zip) uncompressed_name="${filename%.zip}" ;;
        *)     uncompressed_name="$filename" ;;
    esac

    # Keep .lp as the final semantic extension and insert the factor suffix before it.
    if [[ "$uncompressed_name" == *.lp ]]; then
        stem="${uncompressed_name%.lp}"
        lp_name="${stem}.lp"
    else
        stem="$uncompressed_name"
        lp_name="${stem}.lp"
    fi

    for factor in "${FACTORS[@]}"; do
        # Short, filename-safe factor tag:
        #   1.01 -> 1p01
        #   1.1  -> 1p1
        #   2.0  -> 2p0
        factor_tag="${factor//./p}"

        requested_output="${OUTPUT_DIR}/${stem}_d${factor_tag}.lp"
        actual_output="${requested_output}.gz"

        echo "============================================================"
        echo "Input : $input_file"
        echo "Factor: $factor"
        echo "Output: $actual_output"

        python "$PYTHON_SCRIPT" \
            "$input_file" \
            "$requested_output" \
            --factor "$factor"
    done
done
