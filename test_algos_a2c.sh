#!/bin/bash

export WANDB_MODE=dryrun
export WANDB_CONSOLE=off
export WANDB_SILENT=true

envs=(
  POMDP-heavenhell-episodic-v0
  PO-pos-CartPole-v1
  gv_yaml/gv_four_rooms.7x7.yaml
  gv_yaml/gv_memory.5x5.yaml
  extra-dectiger-v0
  extra-cleaner-v0
  extra-car-flag-v0
)
algos=(
  a2c
  asym-a2c
  asym-a2c-state
)

args=(
  --max-simulation-timesteps 500
  --max-episode-timesteps 100
  --simulation-num-episodes 2
  # --truncated-histories
  # --truncated-histories-n 10
  --normalize-hs-features
  --hs-features-dim 64
)

warnings="-W ignore"
# warnings=""

debug="-m ipdb"
debug=""

[[ "$#" -eq 0 ]] && silent=1 || silent=0
[[ "$#" -eq 0 ]] && echo "running without standard output" || echo "running with standard output"
echo

for env in ${envs[@]}; do
  for algo in ${algos[@]}; do
    cmd="python $warnings $debug ./main_a2c.py $env $algo ${args[@]}"
    echo $cmd
    [[ "$silent" -eq 1 ]] && $cmd > /dev/null || $cmd
    [[ "$?" -eq 0 ]] && echo "SUCCESS" || echo "FAIL"
  done
done

exit 0
