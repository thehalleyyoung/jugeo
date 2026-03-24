#!/usr/bin/env python3
import os
import re

# List of papers to analyze
papers = [
    "paper26-import-graph.tex",
    "paper27-certificate-chains.tex",
    "paper28-bug-detection.tex",
    "paper29-repair-semantics.tex",
    "paper30-semantic-control.tex",
    "paper31-state-space.tex",
    "paper32-sequence-encodings.tex",
    "paper33-text-encodings.tex",
    "paper34-deduction-rules.tex",
    "paper35-partiality-models.tex",
]

# Base directory
base_dir = "/Users/halleyyoung/Documents/jugeo/papers/"

# Python API keywords to look for in lstlisting blocks
python_keywords = ['import ', 'def ', 'class ', 'return ', 'from ']

print("PAPERNUMBER|LINES|LSTLISTINGS|TABLES|PYTHONAPI")

for paper in papers:
    paper_path = os.path.join(base_dir, paper)
    
    # Extract paper number from filename
    paper_num = paper.split('-')[0].replace('paper', '')
    
    try:
        # Read the file
        with open(paper_path, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        
        # Count lines
        lines = content.count('\n')
        if not content.endswith('\n'):
            lines += 1
        
        # Count lstlistings
        lstlistings_count = len(re.findall(r'\\begin\{lstlisting\}', content))
        
        # Count tables
        table_count = len(re.findall(r'\\begin\{table\}', content))
        tabular_count = len(re.findall(r'\\begin\{tabular\}', content))
        total_tables = table_count + tabular_count
        
        # Check for Python API in lstlisting blocks
        # Extract all lstlisting blocks
        lstlisting_blocks = re.findall(r'\\begin\{lstlisting\}(.*?)\\end\{lstlisting\}', content, re.DOTALL)
        
        has_python_api = "No"
        for block in lstlisting_blocks:
            for keyword in python_keywords:
                if keyword in block:
                    has_python_api = "Yes"
                    break
            if has_python_api == "Yes":
                break
        
        print(f"{paper_num}|{lines}|{lstlistings_count}|{total_tables}|{has_python_api}")
    
    except FileNotFoundError:
        print(f"{paper_num}|ERROR: File not found|N/A|N/A|N/A")
    except Exception as e:
        print(f"{paper_num}|ERROR: {str(e)}|N/A|N/A|N/A")
