"""Quick look at the running QA app: url, globals, console errors."""
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0].rsplit("/", 1)[0])
from cdp_tools import app_page  # noqa: E402

with app_page() as page:
    print("url", page.url)
    print(page.evaluate("""() => ({
        mods: [typeof PdfTools, typeof Archive, typeof ConvertTools, typeof Cropper, typeof AiPhoto, typeof FileTree],
        folder: App.state.currentFolder,
        title: document.title })"""))
