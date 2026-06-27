"""探索 rqalpha API"""
import os, inspect

# 检查 rqalpha 是否可导入
try:
    import rqalpha
    print(f"rqalpha version: {rqalpha.__version__}")
except Exception as e:
    print(f"Import error: {e}")
    raise

# run() 函数
try:
    from rqalpha import run
    print(f"\nrun() type: {type(run)}")
    sig = inspect.signature(run)
    print(f"run() signature: {sig}")
    for name, param in sig.parameters.items():
        if param.default is inspect.Parameter.empty:
            print(f"  {name}: REQUIRED")
        else:
            print(f"  {name}: default={param.default!r}")
    if run.__doc__:
        print(f"\nrun() doc (first 600 chars):\n{run.__doc__[:600]}")
except Exception as e:
    print(f"run() error: {e}")

# 查看模块结构
pkg_dir = os.path.dirname(rqalpha.__file__)
print(f"\nrqalpha dir: {pkg_dir}")
for f in sorted(os.listdir(pkg_dir)):
    if f.endswith('.py') and not f.startswith('_'):
        print(f"  {f}")
    elif os.path.isdir(os.path.join(pkg_dir, f)) and not f.startswith('_') and not f.startswith('.'):
        print(f"  {f}/")

# 查看策略 API
try:
    from rqalpha import strategies
    print(f"\nstrategies module: {strategies.__file__ if hasattr(strategies, '__file__') else '?'}")
    for attr in dir(strategies):
        if not attr.startswith('_'):
            print(f"  strategies.{attr}")
except Exception as e:
    print(f"\nstrategies module error: {e}")

# 看是否有 mod 系统
try:
    from rqalpha.mod import rqalpha_mod_sys_accounts
    print(f"\nmod sys_accounts OK")
except Exception as e:
    print(f"\nmod check: {e}")
