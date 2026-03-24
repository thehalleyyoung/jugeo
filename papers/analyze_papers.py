#!/usr/bin/env python3
"""
Analyze papers 26-50 for line counts and lstlisting block counts.
Usage: python3 analyze_papers.py
"""

import os
import re
import sys
from pathlib import Path

def main():
    papers_dir = Path(__file__).parent
    os.chdir(papers_dir)
    
    # Known short papers that are expected to be shorter
    known_short = {27, 28, 29, 30, 32, 42, 49, 50}
    
    results = []
    
    # Analyze papers 26-50
    for i in range(26, 51):
        # Find file matching pattern paper##-*.tex
        files = list(papers_dir.glob(f'paper{i:02d}-*.tex'))
        # Filter out backup files
        files = [f for f in files if f.suffix == '.tex' and not f.name.endswith('.bak')]
        
        if not files:
            continue
        
        filepath = files[0]
        
        try:
            # Read file and count lines and lstlisting blocks
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
                lines = len(content.splitlines())
                lstlisting_count = len(re.findall(r'\\begin\{lstlisting\}', content))
        except Exception as e:
            print(f"Error reading {filepath}: {e}", file=sys.stderr)
            continue
        
        flags = []
        
        # Determine flags based on rules
        if i in known_short:
            # Known short paper - flag if fewer than 1200 lines
            if lines < 1200:
                flags.append("SHORT")
        else:
            # Regular paper - should have at least 2 lstlisting blocks
            if lstlisting_count < 2:
                flags.append("LOW_LST")
        
        results.append({
            'num': i,
            'file': filepath.name,
            'lines': lines,
            'lstlisting': lstlisting_count,
            'flags': ', '.join(flags) if flags else ''
        })
    
    # Print results as formatted table
    print("\n" + "="*110)
    print(f"{'#':<3} {'Filename':<40} {'Lines':>8} {'Lst':>4} {'Flags':<20}")
    print("="*110)
    
    for r in results:
        flags_str = r['flags'] if r['flags'] else ''
        print(f"{r['num']:<3} {r['file']:<40} {r['lines']:>8} {r['lstlisting']:>4} {flags_str:<20}")
    
    print("="*110)
    
    # Print summary statistics
    short_papers = [r for r in results if r['num'] in known_short]
    long_papers = [r for r in results if r['num'] not in known_short]
    
    print(f"\nSummary:")
    print(f"  Total papers analyzed: {len(results)}")
    print(f"  Known-short papers:    {len(short_papers)}")
    print(f"  Regular papers:        {len(long_papers)}")
    
    # Find flagged papers
    flagged = [r for r in results if r['flags']]
    
    if flagged:
        print(f"\n⚠️  FLAGGED PAPERS ({len(flagged)}):")
        print("-" * 70)
        for r in flagged:
            if r['num'] in known_short:
                print(f"  Paper {r['num']:2d}: {r['lines']:5d} lines (expected short, minimum 1200)")
            else:
                print(f"  Paper {r['num']:2d}: {r['lstlisting']:2d} lstlisting blocks (minimum 2)")
    else:
        print("\n✓ All papers meet baseline criteria!")
    
    # Statistics
    if results:
        avg_lines = sum(r['lines'] for r in results) / len(results)
        avg_lst = sum(r['lstlisting'] for r in results) / len(results)
        min_lines_paper = min(results, key=lambda x: x['lines'])
        max_lines_paper = max(results, key=lambda x: x['lines'])
        
        print(f"\nStatistics:")
        print(f"  Average lines:        {avg_lines:6.0f}")
        print(f"  Average lstlisting:   {avg_lst:6.2f}")
        print(f"  Min lines:            {min_lines_paper['lines']:6d} (paper {min_lines_paper['num']})")
        print(f"  Max lines:            {max_lines_paper['lines']:6d} (paper {max_lines_paper['num']})")

if __name__ == '__main__':
    main()
