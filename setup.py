"""
立直麻将读牌应用 - 安装配置
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="tenhou-handreading",
    version="0.1.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="基于天凤凤凰桌牌谱数据的立直麻将读牌应用",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/yourusername/tenhou-handreading",
    packages=find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Development Status :: 3 - Alpha",
        "Intended Audience :: End Users/Desktop",
        "Topic :: Games/Entertainment :: Board Games",
    ],
    python_requires=">=3.10",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "tenhou-handreading=src.gui_app:main",
        ],
    },
)
