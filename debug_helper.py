import pathlib


def save_debug_log(content, base_filename, suffix="_analysis", sub_folder="debug_logs", extension=".md"):
    """
    Common function to save debug data to a specific folder.
    """
    # 1. Ensure the directory exists
    debug_dir = pathlib.Path(sub_folder)
    debug_dir.mkdir(exist_ok=True)

    # 2. Build the full path
    # Removes existing extension from base_filename to avoid 'file.pdf.md'
    clean_name = pathlib.Path(base_filename).stem
    debug_file = debug_dir / f"{clean_name}{suffix}{extension}"

    # 3. Save the content
    debug_file.write_text(content, encoding="utf-8")

    print(f"📁 DEBUG SAVED: {debug_file}")
    return debug_file