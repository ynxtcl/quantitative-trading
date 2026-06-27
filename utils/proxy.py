"""Proxy management"""
import os, urllib.request, logging; logger = logging.getLogger(__name__)
PROXY_KEYS = ['HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy','ALL_PROXY','all_proxy']
def check_alive(url, t=2.0):
    try: urllib.request.urlopen(url, timeout=t); return True
    except: return False
def safe_clean_proxy():
    url = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY') or os.environ.get('https_proxy') or os.environ.get('http_proxy')
    if not url: return
    if check_alive(url): logger.info(f"Proxy {url} alive - keep"); return
    logger.warning(f"Proxy {url} dead - clean")
    for k in PROXY_KEYS: os.environ.pop(k, None)
    os.environ['no_proxy'] = '*'
