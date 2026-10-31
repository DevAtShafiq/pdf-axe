"""
test_apostille.py  -  Apostille pipeline diagnostic & runner.

Usage (open Cursor terminal, cd to folder first):
    cd "D:\\business docs\\Business Automation\\bulk_folder_maker"

    -- FULL AUTO (recommended) --
    python test_apostille.py --auto "D:\\Students\\John Doe"
    python test_apostille.py --auto "D:\\Students\\John Doe" --out "D:\\Output"

    -- STEP-BY-STEP --
    python test_apostille.py https://apostille.mygov.bd/verify/TOKEN --match-folder "D:\\Students\\John Doe"
    python test_apostille.py --pdf "path\\ssc.pdf" --match-folder "D:\\Students\\John Doe"

    -- FLAGS --
    --keep      Keep temp folder to inspect downloaded images
    --no-gpt    Skip GPT-4o (download test only, no API cost)
    --out PATH  Where to save merged PDFs (default: FOLDER/_apostille_output)
"""
from __future__ import annotations
import sys, os, argparse, time, shutil, tempfile

SEP  = "-" * 70
SEP2 = "=" * 70

def hr(title=""):
    if title:
        pad = max(1, (68 - len(title)) // 2)
        print("\n" + "-"*pad + " " + title + " " + "-"*pad)
    else:
        print("\n" + SEP)

def ok(msg):   print("  [OK]  " + msg)
def warn(msg): print("  [!!]  " + msg)
def err(msg):  print("  [XX]  " + msg)
def info(msg): print("        " + msg)
def log(msg):  print("  |  "   + msg)

def file_summary(path):
    try:
        kb = os.path.getsize(path) / 1024
        sz = f"{kb:.1f} KB" if kb < 1024 else f"{kb/1024:.1f} MB"
    except OSError:
        sz = "?"
    return os.path.basename(path) + "  (" + sz + ")"

def load_api_key():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("OPENAI_API_KEY"):
                    _, _, val = line.partition("=")
                    key = val.strip().strip('"').strip("'")
                    if key:
                        return key
    return os.environ.get("OPENAI_API_KEY")

def main():
    parser = argparse.ArgumentParser(description="Apostille pipeline diagnostic.")
    parser.add_argument("url", nargs="?", help="apostille.mygov.bd URL")
    parser.add_argument("--auto", metavar="FOLDER", help="Full auto-pipeline on a student folder")
    parser.add_argument("--out",  metavar="PATH",   help="Output folder for merged PDFs")
    parser.add_argument("--pdf",  metavar="PATH",   help="Scan a single PDF for its apostille URL")
    parser.add_argument("--match-folder", metavar="PATH", help="Folder for cert/transcript matching")
    parser.add_argument("--keep",   action="store_true", help="Keep temp folder after run")
    parser.add_argument("--no-gpt", action="store_true", help="Skip GPT-4o")
    args = parser.parse_args()

    print("\n" + SEP2)
    print("  APOSTILLE PIPELINE DIAGNOSTIC")
    print(SEP2)

    # import module
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    try:
        import apostille_matcher as am
        ok("apostille_matcher.py imported OK")
    except Exception as e:
        err("Cannot import apostille_matcher: " + str(e))
        sys.exit(1)

    # API key
    api_key = None
    if not args.no_gpt:
        api_key = load_api_key()
        if api_key:
            ok("OpenAI API key loaded  (..." + api_key[-6:] + ")")
        else:
            warn("No OPENAI_API_KEY in .env — GPT-4o will be skipped")

    # =========================================================================
    # --auto mode
    # =========================================================================
    if args.auto:
        folder = os.path.abspath(args.auto)
        out_folder = os.path.abspath(args.out) if args.out else os.path.join(folder, "_apostille_output")
        if not os.path.isdir(folder):
            err("Folder not found: " + folder)
            sys.exit(1)
        if not api_key:
            err("--auto requires OPENAI_API_KEY in .env (OPENAI_API_KEY=sk-...)")
            sys.exit(1)

        hr("FULL AUTO PIPELINE")
        info("Student folder : " + folder)
        info("Output folder  : " + out_folder)
        print()

        t0 = time.time()
        results = am.process_student_folder(folder=folder, output_folder=out_folder, api_key=api_key, log=log)
        elapsed = time.time() - t0

        hr("SUMMARY")
        for r in results:
            if r.get("error"):
                err(str(r.get("apostille_url", "?")) + "  ->  " + str(r["error"]))
            else:
                parts = ["apostille"]
                if r.get("certificate_pdf"): parts.append("certificate")
                if r.get("transcript_pdf"):  parts.append("transcript")
                merged = r.get("merged_pdf")
                if merged:
                    ok(str(r.get("student_name","?")) + "  [" + str(r.get("doc_type","?")) + "]")
                    info("  merged  : " + " + ".join(parts))
                    info("  file    : " + os.path.basename(merged))
                else:
                    warn(str(r.get("student_name","?")) + "  [" + str(r.get("doc_type","?")) + "]  -- no merged PDF")

        print("\n  Total time: " + f"{elapsed:.1f}s")
        print(SEP2 + "\n")
        return

    # =========================================================================
    # Step-by-step mode
    # =========================================================================
    urls_to_test = []  # list of (url, source_pdf_or_none)

    if args.url:
        urls_to_test.append((args.url.strip(), None))

    if args.pdf:
        hr("Scanning PDF for apostille refs")
        pdf_path = os.path.abspath(args.pdf)
        info("PDF: " + pdf_path)
        t0 = time.time()
        refs = am.extract_apostille_refs_from_pdf(pdf_path)
        elapsed = time.time() - t0
        if refs:
            ok("Found " + str(len(refs)) + " apostille ref(s) in " + f"{elapsed:.1f}s")
            for r in refs:
                info("  -> " + r)
                urls_to_test.append((r, pdf_path))
        else:
            warn("No apostille refs found (no QR codes, text or hyperlinks matched)")

    if not urls_to_test:
        err("Nothing to test. Provide a URL, --pdf, or use --auto FOLDER.")
        sys.exit(1)

    for idx, (apostille_url, source_pdf) in enumerate(urls_to_test):
        print("\n" + SEP2)
        print("  TEST " + str(idx+1) + "/" + str(len(urls_to_test)))
        if source_pdf:
            print("  Source PDF : " + os.path.basename(source_pdf))
        print("  URL        : " + apostille_url)
        print(SEP2)

        tmpdir = tempfile.mkdtemp(prefix="sfm_apo_test_")
        info("Temp folder: " + tmpdir)

        try:
            # STEP 1 - download
            hr("STEP 1 -- Download apostille page  (no API)")
            t0 = time.time()
            img_paths = am.get_apostille_images(apostille_url, tmpdir, log=log)
            elapsed = time.time() - t0

            if img_paths:
                ok("Got " + str(len(img_paths)) + " image(s) in " + f"{elapsed:.1f}s")
                for i, p in enumerate(img_paths):
                    label = "screenshot" if "screenshot" in os.path.basename(p) else "doc image " + str(i+1)
                    info("  [" + str(i+1) + "] " + label + " -- " + file_summary(p))
            else:
                err("No images downloaded after " + f"{elapsed:.1f}s")
                info("Possible reasons:")
                info("  * Token expired or page requires login")
                info("  * Playwright not installed  ->  playwright install chromium")
                continue

            # STEP 2 - GPT-4o
            info_dicts = []
            best_info  = {}
            detected   = "other"

            if args.no_gpt or not api_key:
                warn("Skipping GPT-4o  (--no-gpt or no API key)")
            else:
                hr("STEP 2 -- GPT-4o Vision reads images  (API)")
                for i, img_path in enumerate(img_paths):
                    info("\n  Image [" + str(i+1) + "]: " + os.path.basename(img_path))
                    t0 = time.time()
                    try:
                        extracted = am.read_document_with_gpt4o(img_path, api_key)
                        elapsed = time.time() - t0
                        ok("GPT-4o responded in " + f"{elapsed:.1f}s")
                        for key, val in extracted.items():
                            if val and val not in ("null", "None", None, ""):
                                info("    " + f"{key:<22}" + " : " + str(val))
                        info_dicts.append(extracted)
                    except Exception as e:
                        err("GPT-4o failed for image [" + str(i+1) + "]: " + str(e))

                # merge: first non-null per field wins
                if len(info_dicts) > 1:
                    hr("Merged info")
                    merged_info = {}
                    for d in info_dicts:
                        for k, v in d.items():
                            if k not in merged_info and v not in (None, "null", "None", ""):
                                merged_info[k] = v
                    for key, val in merged_info.items():
                        if val not in (None, "null", "None", ""):
                            info("  " + f"{key:<24}" + " : " + str(val))
                    best_info = merged_info
                elif info_dicts:
                    best_info = info_dicts[0]

                # doc type
                if best_info:
                    detected = am.detect_document_type(best_info)
                    hr("Document type")
                    info("  GPT-4o doc_type    : " + str(best_info.get("doc_type", "-")))
                    info("  Keyword-classified : " + detected)
                    info("  Label              : " + am.DOC_TYPE_LABELS.get(detected, "?"))

                # STEP 3 - find cert + transcript
                match_folder = args.match_folder
                if not match_folder and source_pdf:
                    match_folder = os.path.dirname(source_pdf)

                if match_folder and best_info:
                    hr("STEP 3 -- Find certificate + transcript  (no API)")
                    info("  Folder  : " + match_folder)
                    info("  Student : " + str(best_info.get("student_name", "?")))
                    info("  Type    : " + detected)

                    doc_set = am.find_document_set(
                        match_folder, detected,
                        student_name=best_info.get("student_name") or "",
                    )
                    cert_p  = doc_set.get("certificate")
                    trans_p = doc_set.get("transcript")

                    if cert_p:
                        ok("Certificate : " + os.path.relpath(cert_p, match_folder))
                    else:
                        warn("Certificate : not found")
                    if trans_p:
                        ok("Transcript  : " + os.path.relpath(trans_p, match_folder))
                    else:
                        warn("Transcript  : not found")

                    # STEP 4 - merge
                    ordered = [p for p in [cert_p, trans_p] if p]
                    if img_paths and ordered:
                        hr("STEP 4 -- Merge bundle  (no API)")
                        student    = am._safe_fname(best_info.get("student_name") or "Unknown")
                        dtype_lbl  = am.DOC_TYPE_LABELS.get(detected, "Document")
                        out_dir    = os.path.join(match_folder, "_apostille_output")
                        os.makedirs(out_dir, exist_ok=True)
                        out_path   = os.path.join(out_dir, am._safe_fname(student + " - " + dtype_lbl + " with apostille.pdf"))
                        try:
                            am.merge_apostille_bundle(img_paths, ordered, out_path)
                            parts = ["apostille"]
                            if cert_p:  parts.append("certificate")
                            if trans_p: parts.append("transcript")
                            ok("Merged PDF : " + os.path.basename(out_path))
                            info("  Contents : " + " + ".join(parts))
                            info("  Saved to : " + out_path)
                        except Exception as e:
                            err("Merge failed: " + str(e))
                    elif img_paths:
                        warn("No certificate or transcript found -- skipping merge")

                elif not match_folder:
                    hr("STEP 3 -- Find cert/transcript")
                    warn("No --match-folder provided -- skipping")
                    info('Re-run with:  --match-folder "D:\\path\\to\\student\\folder"')

        finally:
            if args.keep:
                print("\n  Temp folder kept at:\n  " + tmpdir)
            else:
                shutil.rmtree(tmpdir, ignore_errors=True)

    print("\n" + SEP2)
    print("  DONE")
    print(SEP2 + "\n")

if __name__ == "__main__":
    main()
