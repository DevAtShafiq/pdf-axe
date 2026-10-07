"""Tests for rename_templates (document-name template suggestions, languages)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import rename_templates as rt  # noqa: E402

OLD_FILE = (
    "# Custom document rename templates (English-Korean, one per line).\n"
    "# One side may be blank for English-only or Korean-only templates.\n"
    "\n"
    "Recommendation Letter From High School Principal-고등학교 교장 추천서\n"
    "bachelor certificate & transcript with-학부 졸업증명서 및 성적증명서(아포스티유 포합)\n"
    "bachelor certificate-\n"
    "-가족사진\n"
)


@pytest.fixture
def store(tmp_path):
    return rt.TemplateStore(str(tmp_path))


@pytest.fixture
def old_store(tmp_path):
    (tmp_path / rt.USER_FILE_NAME).write_text(OLD_FILE, encoding="utf-8")
    return rt.TemplateStore(str(tmp_path))


# ── parsing ────────────────────────────────────────────────────────────────

def test_parse_old_format_splits_on_last_hyphen():
    pairs = rt.parse_pairs("e-apostille transcript-아포스티유 성적증명서\n# c\n\nfoo-\n-바\n")
    assert pairs == [("e-apostille transcript", "아포스티유 성적증명서"), ("foo", ""), ("", "바")]


def test_parse_line_without_hyphen_is_one_sided():
    assert rt.parse_pairs("Passport\n여권\n") == [("Passport", ""), ("", "여권")]


def test_builtin_lists_loaded():
    ko = rt.TemplateStore.builtin_pairs("ko")
    assert ("passport", "여권") in ko
    assert len(ko) >= 80
    en = rt.TemplateStore.builtin_pairs("en")
    assert ("Passport", "") in en and ("Birth Certificate", "") in en
    assert all(loc == "" for _, loc in en)


def test_old_user_file_still_works_in_korean_mode(old_store):
    rows = old_store.rows("ko")
    custom = [(r.en, r.local) for r in rows if r.source == "custom"]
    assert custom[0] == ("Recommendation Letter From High School Principal", "고등학교 교장 추천서")
    assert ("bachelor certificate", "") in custom
    assert ("", "가족사진") in custom
    # built-ins follow the user rows
    assert any(r.en == "passport" and r.builtin for r in rows)


def test_international_mode_lists_english_only(old_store):
    rows = old_store.rows("en")
    names = [r.en for r in rows]
    assert names[0] == "bachelor certificate"  # English-only custom row first
    assert "Passport" in names
    # English side of KR-EN rows is derived; no duplicates (case-insensitive)
    assert "Recommendation Letter From High School Principal" in names
    slugs = [rt.slug(n) for n in names]
    assert len(slugs) == len(set(slugs))
    assert "" not in names


# ── stems ──────────────────────────────────────────────────────────────────

def test_stem_per_mode():
    assert rt.stem_for("passport", "여권", "ko") == "여권-passport"
    assert rt.stem_for("passport", "여권", "en") == "passport"
    assert rt.stem_for("Passport", "", "ko") == "Passport"
    assert rt.stem_for("", "여권", "en") == "여권"
    assert rt.stem_for('a/b: c?', "", "en") == "a_b_ c_"


def test_unknown_language_falls_back_to_default():
    assert rt.normalize_lang("xx") == rt.DEFAULT_LANG
    assert rt.normalize_lang("EN") == "en"


# ── scoring / slug matching ────────────────────────────────────────────────

def _top(store, q, lang="en", n=1):
    return [r.en for _p, r, _s in store.filter_scored(q, lang)[:n]]


@pytest.mark.parametrize("q,expected", [
    ("passport", "Passport"),
    ("Passport", "Passport"),
    ("pass", "Passport"),
    ("passprt", "Passport"),
    ("pasport", "Passport"),
    ("birth cert", "Birth Certificate"),
    ("birth_certificate", "Birth Certificate"),
    ("BIRTH-CERTIFICATE", "Birth Certificate"),
    ("bank stmt", "Bank Statement"),
    ("여권", "Passport"),
])
def test_slugish_queries_international(store, q, expected):
    assert _top(store, q) == [expected]


def test_korean_mode_matches_korean_and_english(store):
    assert store.filter_scored("여권", "ko")[0][2] == "여권-passport"
    assert store.filter_scored("passport", "ko")[0][2] == "여권-passport"
    assert store.filter_scored("여권-passport", "ko")[0][0] == 0


def test_empty_query_is_browse_list(store):
    res = store.filter_scored("", "en")
    assert len(res) > 20 and all(p == 0 for p, _r, _s in res)


def test_random_name_has_no_match(store):
    assert store.filter_scored("whatsapp387937498", "en") == []


def test_suggestions_include_extension_and_strip_typed_ext(store):
    s = store.suggestions("passport.pdf", "en", ".pdf")[0]
    assert s["name"] == "Passport.pdf" and s["stem"] == "Passport" and s["builtin"]
    s = store.suggestions("passport", "ko", "pdf")[0]
    assert s["name"] == "여권-passport.pdf"
    assert s["label"] == "여권  ·  passport"


# ── save / load custom rows ────────────────────────────────────────────────

def test_add_and_save_rows_roundtrip(store, tmp_path):
    ok, path = store.add_user_row("ko", "visa grant notice", "비자 발급 확인서")
    assert ok and path == str(tmp_path / rt.USER_FILE_NAME)
    ok, _ = store.add_user_row("en", "Offer Letter Signed")
    assert ok
    assert store.user_pairs("ko") == [("visa grant notice", "비자 발급 확인서"),
                                      ("Offer Letter Signed", "")]
    assert store.user_pairs("en") == [("Offer Letter Signed", "")]
    # adding the same row twice is a no-op
    store.add_user_row("en", "Offer Letter Signed")
    assert store.user_pairs("ko").count(("Offer Letter Signed", "")) == 1
    # file stays in the old English-Korean format
    text = (tmp_path / rt.USER_FILE_NAME).read_text(encoding="utf-8")
    assert "visa grant notice-비자 발급 확인서\n" in text
    assert "Offer Letter Signed-\n" in text
    assert rt.parse_pairs(text) == store.user_pairs("ko")


def test_international_save_keeps_bilingual_rows(old_store):
    ok, _ = old_store.save_user_rows("en", [{"en": "Custom One", "local": "ignored"}])
    assert ok
    ko = old_store.user_pairs("ko")
    assert ("Recommendation Letter From High School Principal", "고등학교 교장 추천서") in ko
    assert ("Custom One", "") in ko
    assert ("bachelor certificate", "") not in ko       # replaced by the en save
    assert old_store.user_pairs("en") == [("Custom One", "")]


def test_korean_save_replaces_file(old_store):
    ok, _ = old_store.save_user_rows("ko", [("a", "가"), {"en": "b", "local": ""}, ("", "")])
    assert ok
    assert old_store.user_pairs("ko") == [("a", "가"), ("b", "")]


def test_custom_row_is_suggested_first(store):
    store.add_user_row("en", "Passport Copy Notarized")
    assert _top(store, "passport copy") == ["Passport Copy Notarized"]


def test_pair_from_typed_name():
    assert rt.pair_from_name_text("여권-passport", "ko") == ("passport", "여권")
    assert rt.pair_from_name_text("passport-여권", "ko") == ("passport", "여권")
    assert rt.pair_from_name_text("e-visa", "ko") == ("e-visa", "")
    assert rt.pair_from_name_text("여권-passport", "en") == ("여권-passport", "")


# ── unique names ───────────────────────────────────────────────────────────

def test_unique_name_suffixes(tmp_path):
    d = str(tmp_path)
    assert rt.unique_name(d, "Passport", ".pdf") == "Passport.pdf"
    (tmp_path / "Passport.pdf").write_bytes(b"x")
    assert rt.unique_name(d, "Passport", ".pdf") == "Passport (2).pdf"
    (tmp_path / "Passport (2).pdf").write_bytes(b"x")
    assert rt.unique_name(d, "Passport", "pdf") == "Passport (3).pdf"


def test_unique_name_ignores_the_source_file(tmp_path):
    src = tmp_path / "Passport.pdf"
    src.write_bytes(b"x")
    assert rt.unique_name(str(tmp_path), "Passport", ".pdf", src_path=str(src)) == "Passport.pdf"


def test_strip_ext():
    assert rt.strip_ext("passport.pdf", ".pdf") == "passport"
    assert rt.strip_ext("passport.PDF", "pdf") == "passport"
    assert rt.strip_ext(".pdf", ".pdf") == ".pdf"
    assert rt.strip_ext("passport", "") == "passport"


# ── OCR checklist types ────────────────────────────────────────────────────

def test_ocr_stem_per_language(store):
    assert store.stem_for_ocr_type("PASSPORT", "", "ko") == "여권-passport"
    assert store.stem_for_ocr_type("PASSPORT", "", "en") == "Passport"
    assert store.stem_for_ocr_type("PASSPORT", "FATHER name", "en") == "father passport"
    assert store.stem_for_ocr_type("BANK STATEMENT", "", "en") == "Bank Statement"
    fn = store.ocr_stem_fn("ko")
    assert fn("BIRTH CERTIFICATE", "") == "지원자 출생증명서-applicant birth certificate"
    assert store.stem_for_ocr_type("", "", "en") is None


# ── bridge wiring ──────────────────────────────────────────────────────────

@pytest.fixture
def bridge(tmp_path):
    import sfm_bridge
    b = sfm_bridge.SFMBridge()
    b._SETTINGS_FILE = str(tmp_path / "settings.json")   # never the real settings
    old = rt.store()
    (tmp_path / "app").mkdir()
    rt.set_base_dir(str(tmp_path / "app"))                # never the real templates file
    yield b
    rt._default_store = old


def test_bridge_language_setting(bridge):
    assert bridge.get_rename_lang()["lang"] == rt.DEFAULT_LANG
    assert bridge.set_rename_lang("en")["ok"]
    assert bridge.get_rename_lang()["lang"] == "en"
    assert not bridge.set_rename_lang("xx")["ok"]
    r = bridge.get_rename_templates()
    assert r["ok"] and r["lang"] == "en" and r["rows"][0]["stem"] == r["rows"][0]["en"]
    s = bridge.filter_rename_suggestions("passprt", ".pdf")["suggestions"][0]
    assert s["name"] == "Passport.pdf"
    s = bridge.filter_rename_suggestions("passport", ".pdf", "ko")["suggestions"][0]
    assert s["name"] == "여권-passport.pdf"


def test_bridge_rename_with_template_never_overwrites(bridge, tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "Passport.pdf").write_bytes(b"existing")
    src = d / "whatsapp387937498.pdf"
    src.write_bytes(b"new")
    bridge.set_rename_lang("en")
    r = bridge.rename_with_template(str(src), "Passport")
    assert r["ok"], r
    assert r["new_name"] == "Passport (2).pdf"
    assert (d / "Passport.pdf").read_bytes() == b"existing"
    assert (d / "Passport (2).pdf").read_bytes() == b"new"


def test_bridge_rename_and_save_template(bridge, tmp_path):
    d = tmp_path / "docs2"
    d.mkdir()
    src = d / "IMG_0001.jpg"
    src.write_bytes(b"x")
    r = bridge.rename_with_template(str(src), "비자-visa grant.jpg", True, "ko")
    assert r["ok"] and r["saved"], r
    assert r["new_name"] == "비자-visa grant.jpg"
    assert ("visa grant", "비자") in rt.store().user_pairs("ko")
    assert bridge.save_rename_templates([{"en": "Only English", "local": ""}], "en")["ok"]
    assert ("visa grant", "비자") in rt.store().user_pairs("ko")
    assert rt.store().user_pairs("en") == [("Only English", "")]
    assert bridge.save_name_template("Added Via Bridge", "", "en")["ok"]
    assert ("Added Via Bridge", "") in rt.store().user_pairs("en")
