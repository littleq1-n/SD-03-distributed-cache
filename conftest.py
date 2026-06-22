import os
import sys

# 确保仓库根目录在 sys.path 上,使 `import distcache` / `tests.helpers` 可用
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
