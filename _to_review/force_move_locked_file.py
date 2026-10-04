"""
Force-move a locked file by copying it first, then renaming the original.
Useful when WinError 32 blocks a normal move (file in use by Windows Search/Antivirus).
"""

import os
import shutil
import subprocess

FILE_PATH = r"D:\out of drive\hansung e visa 2026\SEP 2026\ALL 9 SCAN COPY\HASAN MD MAHMUDUL\여권-passport.pdf"

def find_locking_process(filepath):
    """Try to find which process is locking the file using handle.exe or wmic."""
    print("\n🔍 Checking which process has the file locked...")
    try:
        result = subprocess.run(
            ["handle.exe", "-nobanner", filepath],
            capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            print("Locking process(es):\n", result.stdout)
        else:
            print("  handle.exe not found or no lock info available.")
    except Exception:
        pass

    # Try wmic as fallback
    try:
        result = subprocess.run(
            f'wmic process where "CommandLine like \'%passport%\'" get Name,ProcessId,CommandLine',
            shell=True, capture_output=True, text=True, timeout=10
        )
        if result.stdout.strip():
            print("  Processes with 'passport' in command line:\n", result.stdout)
    except Exception:
        pass


def force_move(filepath):
    filename_no_ext = os.path.splitext(os.path.basename(filepath))[0]
    parent_dir = os.path.dirname(filepath)
    dest_folder = os.path.join(parent_dir, filename_no_ext)
    dest_file = os.path.join(dest_folder, os.path.basename(filepath))

    print(f"\n📄 Source : {filepath}")
    print(f"📁 Target : {dest_folder}")

    # Check source exists
    if not os.path.exists(filepath):
        print("\n❌ File not found! Check the path.")
        return

    # Create destination folder
    os.makedirs(dest_folder, exist_ok=True)
    print("✅ Destination folder ready.")

    # Step 1: Try normal move first
    print("\n⏳ Trying normal move...")
    try:
        shutil.move(filepath, dest_file)
        print(f"✅ SUCCESS! File moved to:\n   {dest_file}")
        return
    except PermissionError as e:
        print(f"  Normal move blocked: {e}")

    # Step 2: Try copy + rename original
    print("\n⏳ Trying copy-then-rename approach...")
    try:
        shutil.copy2(filepath, dest_file)
        print("  ✅ Copy succeeded.")

        # Rename original to mark it as processed (not deleted)
        renamed = filepath + ".MOVED_COPY_OK"
        os.rename(filepath, renamed)
        print(f"  ✅ Original renamed to: {os.path.basename(renamed)}")
        print(f"\n✅ SUCCESS! File is now at:\n   {dest_file}")
        print(f"\n⚠️  Original is renamed (not deleted): {renamed}")
        print("   You can delete it manually once you confirm the copy is good.")
        return
    except Exception as e:
        print(f"  Copy+rename also failed: {e}")

    # Step 3: Use robocopy (Windows built-in, very robust)
    print("\n⏳ Trying robocopy (Windows built-in)...")
    try:
        result = subprocess.run(
            ["robocopy", parent_dir, dest_folder,
             os.path.basename(filepath), "/MOVE", "/R:3", "/W:2"],
            capture_output=True, text=True
        )
        # robocopy exit codes 0-7 are success/partial success
        if result.returncode <= 7:
            print(f"✅ robocopy SUCCESS!\n{result.stdout}")
        else:
            print(f"  robocopy failed (exit {result.returncode}):\n{result.stderr}")
    except Exception as e:
        print(f"  robocopy error: {e}")
        find_locking_process(filepath)
        print("\n💡 TIP: Try restarting Windows Search:")
        print('   Open PowerShell as Admin and run:')
        print('   net stop "Windows Search" && net start "Windows Search"')
        print("   Then run this script again.")


if __name__ == "__main__":
    force_move(FILE_PATH)
    input("\nPress Enter to exit...")
