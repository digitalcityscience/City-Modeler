#!/bin/bash
# DB-less headless regression (building regression set), Git Bash on Windows.
# Usage: run_regression.sh <out_dir> [--cac] [test_xxx.py ...]   (no tests = whole set; --cac adds the CAC suite)
# Runs 4 Blenders in parallel (--factory-startup, own throwaway profile each); each test gets
# the arguments its docstring asks for. Prints "<exit> <test>" lines and "FAILED: n of m".
# Paths come from the environment: BLENDER, CITY_MODELER_SAMPLES (sample buildings folder),
# CITY_MODELER_PERF_DATA (default <repo>/../perf-data), CITYDOCTOR_VALIDATE_SH (optional).
REPO=$(cd "$(dirname "$0")/../.." && pwd)
H=$REPO/scripts/headless
BL="${BLENDER:-/c/Program Files/Blender Foundation/Blender 5.2/blender.exe}"
BD=$(cygpath -w "${CITY_MODELER_SAMPLES:-$HOME/Desktop/blender_test_files/buildings}")
PERF=$(cygpath -w "${CITY_MODELER_PERF_DATA:-$REPO/../perf-data}")
TILE="$PERF\real\hh2026\tiles\LoD2_32_564_5934_1_HH.gml"
TF=$(cygpath -m "$REPO")/test_files/Building
R2=Railway_Scene_LoD3_CityGML2_Building_only.gml
R3=Railway_Scene_LoD3_CityGML3_Building_only.gml
OUT=$1; shift
mkdir -p "$OUT"
WOUT=$(cygpath -w "$OUT")
export PATH="/c/ProgramData/anaconda3/Library/bin:$PATH"
# Add-on = this checkout (works from a git worktree too): bscripts/addons/<name> junction.
BS="$(cygpath -u "$TEMP")/cgbs_$(basename "$REPO")"
if [ ! -e "$BS/addons/city_modeler/__init__.py" ]; then
  mkdir -p "$BS/addons"
  powershell -NoProfile -Command "New-Item -ItemType Junction -Path '$(cygpath -w "$BS/addons/city_modeler")' -Target '$(cygpath -w "$REPO")'" > /dev/null
fi
[ -e "$BS/addons/city_modeler/__init__.py" ] || { echo "no add-on junction in $BS"; exit 2; }
export BLENDER_USER_SCRIPTS="$(cygpath -w "$BS")"
export PYTHONPATH="$(cygpath -w $H/pydeps)"
export CITYDOCTOR_VALIDATE_SH="${CITYDOCTOR_VALIDATE_SH:-}"
CAC=0
if [ "$1" = "--cac" ]; then CAC=1; shift; fi  # also run run_cac_suite.py afterwards
if [ $# -gt 0 ]; then TESTS="$*"; else
TESTS=$(cd $H && ls test_b0[124-9]_*.py test_b1[2-9]_*.py test_b2*_*.py test_b3*_*.py test_b4*_*.py test_b5*_*.py test_b6*_*.py test_b7*_*.py test_b8*_*.py test_facade0*.py test_ticket*.py test_t01_landing.py test_t08_browse.py test_t11d_builder.py test_t11d_cleanup.py test_t14_*.py test_wdu02_*.py test_export_error0[235]_*.py test_semantic_explode.py 2>/dev/null | grep -Ev "b10_|b84_")  # DB tests
fi
run() {
  t=$1; n=${t%.py}; o="$WOUT\\$n"; mkdir -p "$OUT/$n"
  case $n in
    test_b10[0-9]_*|test_cj0*|test_b0*|test_b20*|test_b22*|test_b23*|test_b25*|test_b28*|test_b5*|test_b6*|test_b81*|test_b88*|test_t14*) a="$o";;
    test_b14*) a="$BD $TILE";;
    test_b1*|test_b2*) a="$BD";;
    test_t01*) a="$BD $o";;
    test_t08_browse) a="$PERF $BD $o --skip-large";;
    test_semantic_explode|test_ticket19*) a="$TF/$( [ $n = test_ticket19_alt_opening_spelling ] && echo AltOpeningSpelling_LoD3_CityGML3.gml || echo $R3)";;
    test_ticket07*|test_ticket09*|test_ticket14*) a="$TF/$R3 $o";;
    test_ticket10*) a="$TF/$R2";;
    test_ticket11*) a="$TF/$R2 $o";;
    test_ticket12*) a="$TF/$R2 $TF/$R3 $o";;
    test_ticket13_legacy*) a="$TF/$R2 2.0 $o\cand.gml";;
    *) a="";;
  esac
  # Own short profile per run: --factory-startup must never see the user's extensions
  # (Blender's wheel sync strips pyproj/lxml there). The add-on comes from BLENDER_USER_SCRIPTS.
  local prof="$(cygpath -w "$TEMP")\\cgr$RANDOM$RANDOM"
  export BLENDER_USER_RESOURCES="$prof" BLENDER_USER_CONFIG="$prof\\config"
  "$BL" -b --factory-startup --python-use-system-env --python-exit-code 1 --python "$(cygpath -w $H/$t)" -- $a > "$OUT/$n.log" 2>&1
  echo "$? $n" >> "$OUT/summary.txt"
  rm -rf "$(cygpath -u "$prof")"
}
export -f run; export OUT WOUT BL BD TILE PERF H TF R2 R3
: > "$OUT/summary.txt"
echo $TESTS | tr ' ' '\n' | xargs -P 4 -I{} bash -c 'run {}'
sort -k2 "$OUT/summary.txt" | awk '{print} $1!=0{f++} END{print "FAILED:", f+0, "of", NR}'
if [ $CAC = 1 ]; then
  prof="$(cygpath -w "$TEMP")\cgrcac$RANDOM"
  BLENDER_USER_RESOURCES="$prof" BLENDER_USER_CONFIG="$prof\config" "$BL" --background --factory-startup \
    --python-use-system-env --python "$(cygpath -w $H/run_cac_suite.py)" -- "$WOUT\cac" > "$OUT/cac.log" 2>&1
  echo "CAC exit $?"; grep "^\[cac\] {" "$OUT/cac.log"
  rm -rf "$(cygpath -u "$prof")"
fi
