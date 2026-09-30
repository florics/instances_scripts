#!/usr/bin/env bash
set -euo pipefail

# -----------------------------------------------------------------------------
# User settings
# -----------------------------------------------------------------------------

PYTHON_SCRIPT="/scratch/htc/lmehl/master_thesis/instances_scripts/create_scenario_instances/multiply_demands.py"
OUTPUT_DIR="/scratch/opt/lmehl/instances/2ssp/single_scenarios/factors1p1and1p3_onelec_factor1p2_on3carriers"

# Plain .lp, .lp.gz, and single-file .zip inputs are supported.
INSTANCES=(
    "/scratch/opt/lmehl/instances/base/2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged.lp.gz"
    "/scratch/opt/lmehl/instances/base/2050-100-el_eff-nothingmodified.lp.gz"
)

# Electricity is modified in every output. Each subset of the remaining
# carriers is modified, producing eight LPs per input instance.
ELECTRICITY_FACTOR="1.3"
OTHER_CARRIERS_FACTOR="1.2"
OTHER_CARRIERS=(
    "heat_decentral"
    "heat_central"
    "h2"
)

# -----------------------------------------------------------------------------
# Run experiments
# -----------------------------------------------------------------------------

mkdir -p "$OUTPUT_DIR"

electricity_factor_tag="${ELECTRICITY_FACTOR//./p}"
other_carriers_factor_tag="${OTHER_CARRIERS_FACTOR//./p}"

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

    for combination in {0..7}; do
        output_tag="_electricity${electricity_factor_tag}"
        carrier_args=(--carrier-factor electricity "$ELECTRICITY_FACTOR")

        for index in "${!OTHER_CARRIERS[@]}"; do
            if (( combination & (1 << index) )); then
                carrier="${OTHER_CARRIERS[index]}"
                output_tag+="_${carrier}${other_carriers_factor_tag}"
                carrier_args+=(--carrier-factor "$carrier" "$OTHER_CARRIERS_FACTOR")
            fi
        done

        requested_output="${OUTPUT_DIR}/${stem}${output_tag}.lp"
        actual_output="${requested_output}.gz"

        echo "============================================================"
        echo "Input : $input_file"
        echo "Output: $actual_output"

        python "$PYTHON_SCRIPT" \
            "$input_file" \
            "$requested_output" \
            "${carrier_args[@]}"
    done
done
