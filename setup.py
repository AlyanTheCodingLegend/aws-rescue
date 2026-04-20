from setuptools import setup, find_packages

with open("requirements.txt") as f:
    install_requires = [
        line.strip()
        for line in f
        if line.strip() and not line.startswith("#") and not line.startswith("moto") and not line.startswith("pytest")
    ]

setup(
    name="aws-rescue",
    version="0.1.0",
    packages=find_packages(),
    install_requires=install_requires,
    entry_points={
        "console_scripts": [
            "rescue-cli=cli.main:cli",
        ],
    },
    extras_require={
        "dev": ["moto[s3,dynamodb,lambda,iam,events]>=5.0.0", "pytest>=8.0.0"],
    },
)
