# miningnews

```bash
mkdir -p /opt/miningnews
cd /opt/miningnews
git clone https://github.com/GermannM3/miningnews.git .
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt && python3 -m playwright install --with-deps
nohup python3 main.py >/dev/null 2>&1 &
```
