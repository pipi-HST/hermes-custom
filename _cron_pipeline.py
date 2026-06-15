#!/usr/bin/env python3
"""
Cron job: theme_scan pipeline
Step 1: maintain_embedding_index
Step 2: run_theme_scan
"""
import sys, json, traceback
sys.path.insert(0, '/Users/pipi/hermes-custom')

def main():
    # Step 1: Maintain embedding index
    print("=" * 60)
    print("Step 1: maintain_embedding_index (apply=True)")
    print("=" * 60)
    
    from agents.capture_agent import load_config
    from agents.embedder import Embedder
    from agents.library_agent import LibraryAgent, DEFAULT_OUTLINE_CHAR_THRESHOLD
    from agents.outliner import Outliner
    
    config = load_config('/Users/pipi/hermes-custom/config.yaml')
    outliner = Outliner(
        api_key=config.llm_api_key,
        model="deepseek-v4-flash",
        base_url=config.llm_base_url,
    )
    agent = LibraryAgent(
        config.vault_path,
        outliner=outliner,
        db_path=config.db_path,
        embedder=Embedder(api_key=config.qwen_api_key),
        min_outline_chars=DEFAULT_OUTLINE_CHAR_THRESHOLD,
    )
    index_result = agent.maintain_embedding_index(apply=True)
    print(json.dumps(index_result, indent=2, ensure_ascii=False, default=str))
    
    # Step 2: Run theme scan
    print()
    print("=" * 60)
    print("Step 2: run_theme_scan(rescan_all=False)")
    print("=" * 60)
    
    from bridge.theme_bridge import run_theme_scan
    scan_result = run_theme_scan(rescan_all=False)
    print(json.dumps(scan_result, indent=2, ensure_ascii=False, default=str))
    
    return scan_result

if __name__ == '__main__':
    try:
        result = main()
    except Exception as e:
        print(f"ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)
