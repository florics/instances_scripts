#!/usr/bin/env bash
# Create equivalent integrated LPs with global investment variables.
set -euo pipefail

TRANSFORMER="/scratch/htc/lmehl/master_thesis/instances_scripts/create_2ssp_instances/replace_nonanticipativity_with_global_invest_vars.py"
INTEGRATED="/scratch/opt/lmehl/instances/2ssp/integrated"
PIPS="$INTEGRATED/set_for_pips"
PIPS_OUTPUT="$PIPS/global_invest_vars"
OUTPUT="$INTEGRATED/global_invest_vars"

SCENARIOS_2=(base elec1p2)
SCENARIOS_4=(base elec1p2 heat_decentral1p2 elec1p2_heat_decentral1p2)
SCENARIOS_8=(
    "${SCENARIOS_4[@]}"
    heat_central1p2 elec1p2_heat_central1p2
    heat_decentral1p2_heat_central1p2 elec1p2_heat_decentral1p2_heat_central1p2
)
SCENARIOS_16=(
    "${SCENARIOS_8[@]}"
    h21p2 elec1p2_h21p2 heat_decentral1p2_h21p2 elec1p2_heat_decentral1p2_h21p2
    heat_central1p2_h21p2 elec1p2_heat_central1p2_h21p2
    heat_decentral1p2_heat_central1p2_h21p2 elec1p2_heat_decentral1p2_heat_central1p2_h21p2
)
SCENARIOS_32=("${SCENARIOS_16[@]}")
for electricity in 1p1 1p3; do
    for suffix in \
        "" \
        _heat_decentral1p2 \
        _heat_central1p2 \
        _h21p2 \
        _heat_decentral1p2_heat_central1p2 \
        _heat_decentral1p2_h21p2 \
        _heat_central1p2_h21p2 \
        _heat_decentral1p2_heat_central1p2_h21p2
    do
        SCENARIOS_32+=("elec${electricity}${suffix}")
    done
done

transform() {
    local input="$1"
    local output="$2"
    shift 2
    local -a command=(python3 "$TRANSFORMER" "$input" "$output")
    local scenario
    for scenario in "$@"; do
        command+=(--scenario "$scenario")
    done
    printf 'Running:'; printf ' %q' "${command[@]}"; printf '\n'
    "${command[@]}"
}

mkdir -p "$PIPS_OUTPUT" "$OUTPUT"

shopt -s nullglob
pips_inputs=("$PIPS"/*.lp.gz)
if ((${#pips_inputs[@]} == 0)); then
    printf 'No .lp.gz files found in %s\n' "$PIPS" >&2
    exit 1
fi

for input in "${pips_inputs[@]}"; do
    name="${input##*/}"
    output="$PIPS_OUTPUT/${name%.lp.gz}_global_invest_vars.lp.gz"
    case "$name" in
        *_2scens.lp.gz) transform "$input" "$output" "${SCENARIOS_2[@]}" ;;
        *_4scens.lp.gz) transform "$input" "$output" "${SCENARIOS_4[@]}" ;;
        *_8scens.lp.gz) transform "$input" "$output" "${SCENARIOS_8[@]}" ;;
        *_16scens.lp.gz) transform "$input" "$output" "${SCENARIOS_16[@]}" ;;
        *_32scens.lp.gz) transform "$input" "$output" "${SCENARIOS_32[@]}" ;;
        *) printf 'Unrecognized set_for_pips instance name: %s\n' "$name" >&2; exit 1 ;;
    esac
done

transform "$INTEGRATED/3cm-br-ivm_base_elec1p01.lp.gz" "$OUTPUT/3cm-br-ivm_base_elec1p01_global_invest_vars.lp.gz" base elec1p01
transform "$INTEGRATED/3cm-br-ivm_base_elec1p03.lp.gz" "$OUTPUT/3cm-br-ivm_base_elec1p03_global_invest_vars.lp.gz" base elec1p03
transform "$INTEGRATED/3cm-br-ivm_base_elec1p05.lp.gz" "$OUTPUT/3cm-br-ivm_base_elec1p05_global_invest_vars.lp.gz" base elec1p05
transform "$INTEGRATED/nomods_base_elec1p01.lp.gz" "$OUTPUT/nomods_base_elec1p01_global_invest_vars.lp.gz" base elec1p01
transform "$INTEGRATED/3cm-br-ivm_base_elec1p01_heatboth1p01_elecheatboth1p01.lp.gz" "$OUTPUT/3cm-br-ivm_base_elec1p01_heatboth1p01_elecheatboth1p01_global_invest_vars.lp.gz" base elec1p01 heatboth1p01 elecheatboth1p01
