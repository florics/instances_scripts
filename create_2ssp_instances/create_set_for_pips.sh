SCRIPT="/scratch/htc/lmehl/master_thesis/instances_scripts/create_2ssp_instances/create_multi_scenario_lp.py"
F12="/scratch/opt/lmehl/instances/2ssp/single_scenarios/factor1p2_on4carriers"
F113="/scratch/opt/lmehl/instances/2ssp/single_scenarios/factors1p1and1p3_onelec_factor1p2_on3carriers"
BASE="/scratch/opt/lmehl/instances/base"
OUT="/scratch/opt/lmehl/instances/2ssp/integrated/set_for_pips"

make_sets() {
  local tag="$1"
  local stem="$2"
  local base_lp="$BASE/$stem.lp.gz"
  local prefix_12="$F12/$tag/$stem"
  local prefix_113="$F113/$stem"
  local -a scenarios

  scenarios=(
    --scenario base "$base_lp"
    --scenario elec1p2 "${prefix_12}_electricity1p2.lp.gz"
  )
  python3 "$SCRIPT" "$OUT/${tag}_2scens.lp.gz" "${scenarios[@]}"

  scenarios+=(
    --scenario heat_decentral1p2 "${prefix_12}_heat_decentral1p2.lp.gz"
    --scenario elec1p2_heat_decentral1p2 "${prefix_12}_electricity1p2_heat_decentral1p2.lp.gz"
  )
  python3 "$SCRIPT" "$OUT/${tag}_4scens.lp.gz" "${scenarios[@]}"

  scenarios+=(
    --scenario heat_central1p2 "${prefix_12}_heat_central1p2.lp.gz"
    --scenario elec1p2_heat_central1p2 "${prefix_12}_electricity1p2_heat_central1p2.lp.gz"
    --scenario heat_decentral1p2_heat_central1p2 "${prefix_12}_heat_decentral1p2_heat_central1p2.lp.gz"
    --scenario elec1p2_heat_decentral1p2_heat_central1p2 "${prefix_12}_electricity1p2_heat_decentral1p2_heat_central1p2.lp.gz"
  )
  python3 "$SCRIPT" "$OUT/${tag}_8scens.lp.gz" "${scenarios[@]}"

  scenarios+=(
    --scenario h21p2 "${prefix_12}_h21p2.lp.gz"
    --scenario elec1p2_h21p2 "${prefix_12}_electricity1p2_h21p2.lp.gz"
    --scenario heat_decentral1p2_h21p2 "${prefix_12}_heat_decentral1p2_h21p2.lp.gz"
    --scenario elec1p2_heat_decentral1p2_h21p2 "${prefix_12}_electricity1p2_heat_decentral1p2_h21p2.lp.gz"
    --scenario heat_central1p2_h21p2 "${prefix_12}_heat_central1p2_h21p2.lp.gz"
    --scenario elec1p2_heat_central1p2_h21p2 "${prefix_12}_electricity1p2_heat_central1p2_h21p2.lp.gz"
    --scenario heat_decentral1p2_heat_central1p2_h21p2 "${prefix_12}_heat_decentral1p2_heat_central1p2_h21p2.lp.gz"
    --scenario elec1p2_heat_decentral1p2_heat_central1p2_h21p2 "${prefix_12}_electricity1p2_heat_decentral1p2_heat_central1p2_h21p2.lp.gz"
  )
  python3 "$SCRIPT" "$OUT/${tag}_16scens.lp.gz" "${scenarios[@]}"

  local level suffix
  for level in 1p1 1p3; do
    for suffix in \
      "" \
      "_heat_decentral1p2" \
      "_heat_central1p2" \
      "_h21p2" \
      "_heat_decentral1p2_heat_central1p2" \
      "_heat_decentral1p2_h21p2" \
      "_heat_central1p2_h21p2" \
      "_heat_decentral1p2_heat_central1p2_h21p2"
    do
      scenarios+=(
        --scenario "elec${level}${suffix}" \
        "${prefix_113}_electricity${level}${suffix}.lp.gz"
      )
    done
  done
  python3 "$SCRIPT" "$OUT/${tag}_32scens.lp.gz" "${scenarios[@]}"
}

make_sets "3cm-br-ivm" "2050-100-el_eff-3costmods-1e9boundsremoved-investvarsmerged"
make_sets "nomods" "2050-100-el_eff-nothingmodified"