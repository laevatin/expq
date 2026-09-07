# expq

A GPU experiment queue for coding agents on one machine. Several Claude Code
or Codex agents can each submit experiments; the queue guarantees at most one
job per GPU, pins `CUDA_VISIBLE_DEVICES`, and gives every run its own output
directory. It is a thin wrapper around [pueue](https://github.com/Nukesor/pueue):
one pueue group per GPU with `parallel = 1`.

No root, no daemon of its own, no Python dependencies.

## Install

Linux (downloads the static pueue binaries, sets up a systemd user service,
creates the groups):

    git clone https://github.com/laevatin/expq && cd expq && ./install.sh

macOS: `brew install pueue && brew services start pueue`, then copy `exp`
onto your PATH and run `exp init --gpus 0`.

## Use

    exp submit --label lr3e-4 -- python train.py --lr 3e-4
    # task 7  20260907-131500-lr3e-4-a1f2  → gpu0  (1 ahead)
    #   run_dir: /path/to/repo/runs/20260907-131500-lr3e-4-a1f2

    exp wait 7            # blocks; exits with the job's exit code
    exp log 7 -n 50       # or: exp log 7 -f
    exp status --active   # or --json
    exp gpus              # who is on which card, queue depth, memory
    exp kill 7
    exp submit --gpu none -- python eval.py     # cpu group, parallel 4
    exp submit --gpu 1 --after 7 -- python eval.py --ckpt runs/.../best.pt

Inside a job these are set: `CUDA_VISIBLE_DEVICES` (one card),
`EXP_GPU`, `EXP_RUN_ID`, `EXP_RUN_DIR` (fresh directory, holds `meta.json`
and, after `exp wait`, `log.txt`).

`exp submit --gpu any` (the default) picks the GPU with the shortest queue.
A card whose memory is in use by something outside the queue is avoided;
if every card is like that, the job is queued anyway with a warning.
Threshold: `EXP_GPU_BUSY_MIB` (default 2000).

## Making agents use it

Copy `AGENTS.md.template` into the research repo as `AGENTS.md` (Codex reads
it) and symlink `CLAUDE.md -> AGENTS.md`. For a hard stop, `hooks/guard.py`
is a Claude Code PreToolUse hook that refuses Bash commands that look like
direct training launches unless they go through `exp submit`.

## Why not X

- **Slurm**: the right tool for a cluster, needs root and a controller.
- **task-spooler**: fine, but a single queue per user and weak JSON output.
- **flock on a lockfile**: no visibility, no ordering, no logs.
- **Ray, Kubernetes**: far too heavy for one box.

## Tests

    uv run --with pytest --no-project python -m pytest tests

They run against a real scratch pueue daemon with a fake `nvidia-smi`.
