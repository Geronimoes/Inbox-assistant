#!/usr/bin/env python3
"""
Update Tasks — lightweight script to perform task housekeeping.
Synchronizes manually checked-off items in TASKS.md to the 'Recently Completed' 
section and updates project detail files. Run frequently (e.g., every 15 mins).
"""
import sys
from pathlib import Path

# Add src to path if needed
sys.path.append(str(Path(__file__).parent))

from task_writer import TaskWriter
from fetch_and_triage import load_config

def main():
    config = load_config()
    task_writer = TaskWriter(config)
    
    # Passing an empty list of new tasks triggers housekeeping and re-writing
    written = task_writer.write_tasks([])
    print(f"Task housekeeping complete. {written} new tasks added (expected 0).")

if __name__ == "__main__":
    main()
