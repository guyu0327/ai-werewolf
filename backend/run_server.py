"""PyInstaller 打包入口：产出 resources/server/server.exe，目标机器无需安装 Python。"""
from app.main import run

if __name__ == "__main__":
    run()
