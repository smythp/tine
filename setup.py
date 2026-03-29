from setuptools import setup, find_packages

setup(
    name="tine",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "tine=tine.cli:main",
        ],
    },
    python_requires=">=3.11",
    install_requires=[
        "Pillow",
        "evdev",
    ],
)
