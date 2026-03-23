from setuptools import setup, find_packages

setup(
    name="jugeo",
    version="0.1.0",
    description="Judgment Geometry — formal verification via sheaf descent and trust algebra",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    entry_points={
        "console_scripts": [
            "jugeo=jugeo.cli.main:main",
        ],
    },
    python_requires=">=3.10",
    install_requires=[],
)
