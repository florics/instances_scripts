#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# User settings
# -----------------------------------------------------------------------------

PYTHON_SCRIPT="/scratch/htc/lmehl/master_thesis/instances_scripts/create_scenario_instances/multiply_demands.py"
OUTPUT_DIR="/scratch/opt/lmehl/instances/2ssp/single_scenarios"

# Plain .lp, .lp.gz, and single-file .zip inputs are supported.
INSTANCES=(
    #"/scratch/opt/lmehl/instances/base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp"
    #"/scratch/opt/lmehl/instances/base/2050-100-el_eff-nothingmodified.lp"
    #"/scratch/opt/lmehl/instances/debug_instances/oemofB3_debug_3costmods_boundsremoved_investvarsmerged.lp"
    "/scratch/opt/lmehl/instances/debug_instances/oemofB3_debug_nothingmodified.lp"
)

CARRIERS=(
    "electricity"
    "heat_decentral"
    #"heat_central"
    #"h2"
)

FACTOR="1.2"
FACTOR_TAG="${FACTOR//./p}"

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

    if [[ "$uncompressed_name" == *.lp ]]; then
        stem="${uncompressed_name%.lp}"
    else
        stem="$uncompressed_name"
    fi

    # The all-1 combination is the unchanged input LP, so it is not recreated.
    for combination in {1..15}; do
        output_tag=""
        carrier_args=()

        for index in "${!CARRIERS[@]}"; do
            if (( combination & (1 << index) )); then
                carrier="${CARRIERS[index]}"
                output_tag+="_${carrier}${FACTOR_TAG}"
                carrier_args+=(--carrier-factor "$carrier" "$FACTOR")
            fi
        done

        requested_output="${OUTPUT_DIR}/${stem}${output_tag}.lp"
        actual_output="${requested_output}.gz"

        echo "============================================================"
        echo "Input : $input_file"
        echo "Output: $actual_output"
        echo "Changed carriers: ${output_tag#_}"

        python "$PYTHON_SCRIPT" \
            "$input_file" \
            "$requested_output" \
            "${carrier_args[@]}"
    done
done
