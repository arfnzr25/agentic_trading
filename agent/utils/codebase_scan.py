
import os
import re

def scan_codebase(root_dir):
    report = []
    
    patterns = {
        "BARE_EXCEPT": (r"except\s*:", "Avoid bare 'except' clauses. Catch specific exceptions."),
        "PRINT_STMT": (r"print\(", "Use structured logging instead of print()."),
        "HARDCODED_PATH": (r"['\"](C:|D:|E:|/Users|/home)['\"]", "Avoid absolute system paths."),
        "MUTABLE_DEFAULT": (r"def\s+\w+\(.*?=\s*(\[\]|\{\})", "Avoid mutable default arguments."),
        "TODO_FIXME": (r"(TODO|FIXME)", "Unresolved TODO/FIXME item."),
        "BROKEN_REL_IMPORT": (r"from \.[a-z_]+ import", "Check relative imports in submodules."),
    }
    
    for root, dirs, files in os.walk(root_dir):
        if "venv" in root or "__pycache__" in root or ".git" in root:
            continue
            
        for file in files:
            if not file.endswith(".py"):
                continue
                
            path = os.path.join(root, file)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                    
                for i, line in enumerate(lines):
                    for key, (pattern, msg) in patterns.items():
                        if re.search(pattern, line):
                            # Filter benign print statements in verification scripts
                            if key == "PRINT_STMT" and "verify_" in file:
                                continue
                            
                            report.append(f"[{key}] {file}:{i+1} - {msg}\n  Line: {line.strip()[:80]}")
            except Exception as e:
                pass
                
    return report

if __name__ == "__main__":
    issues = scan_codebase("v:\\Antigravity Projects\\AgenticTrading\\agent")
    
    with open("agent/scan_report.txt", "w", encoding="utf-8") as f:
        f.write(f"Found {len(issues)} potential issues.\n")
        for issue in issues:
            f.write(issue + "\n")
            
    print(f"Scan complete. Found {len(issues)} issues.")
