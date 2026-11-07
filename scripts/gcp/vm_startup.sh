#!/bin/bash
# Startup script for the two-year-run VM (create_vm.sh passes it in). Google runs it as root at
# every boot. It sets the machine up once, then (re)starts the block's loops as user `uc`
# (scripts/gcp/block.sh). Everything it starts resumes where it stopped, so a reboot is harmless.
set -uo pipefail
exec > >(sed -u 's/^/uc-startup: /' | tee -a /var/log/uc-startup.log) 2>&1
echo "boot $(date -u)"

meta() {
  curl -fs -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/$1"
}
U=uc
H=/home/$U
R=$H/undercurrent
id $U >/dev/null 2>&1 || useradd -m -s /bin/bash $U
as_uc() { sudo -u $U -H bash -lc "export PATH=\$HOME/.local/bin:\$PATH; $1"; }

# 1. The GPU driver (the Deep Learning VM image installs it on first boot, in parallel with this).
for i in $(seq 1 90); do nvidia-smi -L >/dev/null 2>&1 && break; sleep 10; done
if ! nvidia-smi -L >/dev/null 2>&1; then
  echo "ERROR: no working NVIDIA driver after 15 minutes. SSH in and run: sudo /opt/deeplearning/install-driver.sh"
  exit 1
fi
nvidia-smi -L
# PyTorch in uv.lock needs driver 580+ (CUDA 13). Older: install the newest with Google's installer
# (it reboots the VM; this script then runs again).
DRIVER=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)
if [ "${DRIVER%%.*}" -lt 580 ]; then
  echo "driver $DRIVER is older than 580: installing the newest (the VM will reboot)"
  curl -fsSL https://storage.googleapis.com/compute-gpu-installation-us/installer/latest/cuda_installer.pyz -o /root/cuda_installer.pyz
  python3 /root/cuda_installer.pyz install_driver
  exit 0
fi

# 2. Tools, uv and the code.
for i in 1 2 3 4 5 6; do
  command -v git >/dev/null && command -v tmux >/dev/null && break
  apt-get update -q && apt-get install -y -q git tmux && break
  sleep 30  # first boot: apt may be locked by unattended upgrades
done
as_uc 'command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh'
[ -d $R/.git ] || as_uc "git clone -q https://github.com/CooperMcCloskey/undercurrent.git $R"
as_uc "cd $R && git pull -q --ff-only || true; uv sync -q"
echo "GOOGLE_CLOUD_PROJECT=$(meta project/project-id)" >$R/.env
chown $U:$U $R/.env
if ! as_uc "cd $R && uv run python -c 'import torch; assert torch.cuda.is_available(); print(\"torch\", torch.__version__, \"sees\", torch.cuda.device_count(), \"GPU(s)\")'"; then
  echo "ERROR: PyTorch can't use the GPU (driver $DRIVER). SSH in and check: nvidia-smi"
  exit 1
fi

# 3. The block's settings, sized to this machine.
GPUS=$(nvidia-smi -L | wc -l)
MEM_GB=$(free -g | awk '/^Mem:/ {print $2}')
PULLS=$([ "$MEM_GB" -ge 90 ] && echo 3 || echo 2)
BATCH=$([ "$MEM_GB" -ge 30 ] && echo 3 || echo 2)
cat >$H/block.env <<EOF
export BLOCK=$(meta instance/attributes/uc-block)
export PULL_START=$(meta instance/attributes/uc-pull-start)
export OWN_START=$(meta instance/attributes/uc-own-start)
export OWN_END=$(meta instance/attributes/uc-own-end)
export BUCKET=$(meta instance/attributes/uc-bucket)
export MAX_GB=$(meta instance/attributes/uc-max-gb || echo 2500)
export UC_HALF=1 GPUS=$GPUS PULLS=$PULLS BATCH=$BATCH
EOF
chown $U:$U $H/block.env
echo "settings: $(tr '\n' ' ' <$H/block.env)"

# 4. Dry run (first boot only), then the pull, embed and upload loops. Started as its own systemd
# unit that stays "active" after the launcher exits, so the tmux sessions it starts outlive this
# startup script.
mkdir -p $R/logs && chown -R $U:$U $R/logs
systemctl stop uc-block 2>/dev/null; systemctl reset-failed uc-block 2>/dev/null
systemd-run --unit=uc-block --uid=$U --gid=$U -p RemainAfterExit=yes -p KillMode=process \
  --setenv=HOME=$H --working-directory=$R \
  bash -lc "export PATH=$H/.local/bin:\$PATH; bash $R/scripts/gcp/block.sh start >> $R/logs/start.log 2>&1"
echo "started; status: sudo -u uc -H bash $R/scripts/gcp/block.sh status"
