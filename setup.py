import os
import re

from setuptools import find_packages, setup

here = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(here, 'README.md'), 'r', encoding='utf-8') as fh:
    long_description = fh.read()

# The version lives in the package so that it is defined in exactly one place.
with open(os.path.join(here, 'pywebscrapr', '__init__.py'), 'r', encoding='utf-8') as fh:
    version = re.search(r"^__version__\s*=\s*['\"]([^'\"]+)['\"]", fh.read(), re.M).group(1)

setup(
    name='pywebscrapr',
    version=version,
    author='Infinitode Pty Ltd',
    author_email='infinitode.ltd@gmail.com',
    description='An open-source Python library for web scraping tasks. Includes support for both image scraping and text scraping.',
    long_description=long_description,
    long_description_content_type='text/markdown',
    url='https://github.com/infinitode/pywebscrapr',
    packages=find_packages(include=['pywebscrapr', 'pywebscrapr.*']),
    install_requires=[
        'requests>=2.32.4',
        'beautifulsoup4>=4.12',
        'pillow>=10.3.0',
    ],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
    ],
    python_requires='>=3.9',
)
