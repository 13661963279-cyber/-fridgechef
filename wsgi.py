import sys
import os

# Set your API keys here
os.environ["ANTHROPIC_API_KEY"] = "sk-8103972f8be643d2a1d9d6df3d7ebd37"
os.environ["ANTHROPIC_BASE_URL"] = "https://api.deepseek.com/anthropic"

path = "/home/zhuozhuo999/-fridgechef"
if path not in sys.path:
    sys.path.insert(0, path)

from app import app as application
