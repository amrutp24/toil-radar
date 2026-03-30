from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="toil-tracker",
    version="0.1.0",
    author="Amrut Pagidipally",
    author_email="amrut.pagidipally@gmail.com",
    description="Detect, visualize, and reduce DevOps toil",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/amrutp24/toil-tracker",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Intended Audience :: System Administrators",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: System :: Systems Administration",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "toil-tracker=toil_tracker.cli:main",
            "toil-dashboard=toil_tracker.dashboard:main",
        ],
    },
    include_package_data=True,
    zip_safe=False,
)