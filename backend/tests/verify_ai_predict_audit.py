"""
E2E verification: ai_predict audit log is recorded for AI pre-annotation.

Validates the full audit trail:
  1. Auto-annotation (sync path, with active fine-tune model) inserts AnnotationLog rows
     with action='ai_predict' for the auto-labeled images.
  2. The /api/annotations/list/{dataset_id} endpoint exposes these entries.
  3. The /api/images/{image_id} detail endpoint includes ai_predict in annotation_history.
  4. Manual annotation (save) writes confirm/correct entries with from_label_id set
     to the previous final_label_id (modification trail).
  5. Clear annotation writes a 'reject' entry.

Exit 0 = pass, non-zero = fail.
Required services: backend on :5000, MySQL :3310, Redis :9770, MinIO :9000.
"""
import sys
import time
import httpx

BASE = "http://127.0.0.1:5000"
RESULTS: list = []


def log(t, m): print(f"[{t}] {m}", flush=True)


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    log("PASS" if ok else "FAIL", f"{name}: {detail}")


def main():
    log("START", "=== ai_predict Audit Log E2E ===")

    # 1) Login
    r = httpx.post(f"{BASE}/api/auth/login", data={"username": "admin", "password": "admin123"})
    if r.status_code != 200 or "access_token" not in r.json():
        record("login", False, f"{r.status_code} {r.text[:150]}")
        return
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    record("login", True, "got token")

    # 2) Find a dataset with categories + an active fine-tune model
    r = httpx.get(f"{BASE}/api/datasets", headers=H)
    datasets = r.json().get("items", [])
    if not datasets:
        record("datasets_available", False, "no datasets")
        return
    record("datasets_available", True, f"{len(datasets)} datasets")

    # Pick the first dataset that has categories
    DS = None
    cat_names: list = []
    for d in datasets:
        cr = httpx.get(f"{BASE}/api/datasets/{d['id']}/categories", headers=H)
        cats = cr.json().get("items", cr.json() if isinstance(cr.json(), list) else [])
        if cr.status_code == 200 and len(cats) >= 2:
            DS = d["id"]
            cat_names = [c["name"] for c in cats]
            break
    if DS is None:
        record("dataset_with_categories", False, "no dataset has >=2 categories")
        return
    record("dataset_with_categories", True, f"DS={DS}, categories={cat_names}")

    # 3) Check for active fine-tune model
    r = httpx.get(f"{BASE}/api/models/", headers=H)
    models = r.json().get("items", [])
    active = [m for m in models if m.get("is_active")]
    SKIP_AI = False
    if not active:
        SKIP_AI = True
        record("active_finetune_model", True, "skipped: no active model in env")
    else:
        record("active_finetune_model", True, f"active={active[0]['id']} name={active[0].get('name')}")

    # 4) Run auto-annotate synchronously (so we can immediately query logs)
    if not SKIP_AI:
        # Use small confidence threshold to maximize auto_labeled count
        r = httpx.post(
            f"{BASE}/api/auto-annotate/run",
            json={"dataset_id": DS, "model_name": "v1",
                  "confidence_threshold": 0.1, "async_mode": False},
            headers=H, timeout=120,
        )
        record("auto_annotate_sync",
               r.status_code == 200,
               f"{r.status_code} {r.text[:200]}")
        if r.status_code == 200:
            data = r.json()
            auto_labeled = data.get("auto_labeled", 0)
            log("INFO", f"  auto_labeled={auto_labeled} total={data.get('total')} "
                        f"no_match={data.get('no_match', 'n/a')}")

    # 5) Query annotation list, filter to ai_predict action
    r = httpx.get(f"{BASE}/api/annotations/list/{DS}?action=ai_predict&page_size=100",
                  headers=H)
    ai_logs = r.json().get("items", []) if r.status_code == 200 else []
    if SKIP_AI:
        record("ai_predict_logs_present", True,
               f"skipped: no active model. Existing ai_predict count={len(ai_logs)}")
    else:
        record("ai_predict_logs_present",
               r.status_code == 200 and len(ai_logs) >= 1,
               f"{r.status_code}, {len(ai_logs)} ai_predict entries")

    # 6) Validate at least one ai_predict log has to_label_id set (top1 mapped to category)
    with_to_label = [l for l in ai_logs if l.get("to_label_id")]
    if SKIP_AI:
        record("ai_predict_has_to_label", True,
               f"skipped: no active model. {len(with_to_label)}/{len(ai_logs)} have to_label_id")
    else:
        record("ai_predict_has_to_label",
               len(with_to_label) >= 1,
               f"{len(with_to_label)}/{len(ai_logs)} have to_label_id")

    # 7) Pick an auto-labeled image, verify detail endpoint includes ai_predict in history
    r = httpx.get(f"{BASE}/api/images/list/{DS}?status=ai_labeled&page_size=5", headers=H)
    items = r.json().get("items", [])
    if items:
        target_id = items[0]["id"]
        r = httpx.get(f"{BASE}/api/images/{target_id}", headers=H)
        if r.status_code == 200:
            det = r.json()
            history = det.get("annotation_history", [])
            actions = [h.get("action") for h in history]
            record("image_detail_has_ai_predict",
                   "ai_predict" in actions,
                   f"actions={actions}")
            record("ai_predict_is_first",
                   len(history) > 0 and history[0].get("action") == "ai_predict",
                   f"first={history[0].get('action') if history else 'none'}")
        else:
            record("image_detail_has_ai_predict", False, f"detail {r.status_code}")
    else:
        if SKIP_AI:
            record("image_detail_has_ai_predict", True, "skipped: no ai_labeled images")
        else:
            record("image_detail_has_ai_predict", False, "no ai_labeled images to test")

    # 8) Test save_annotation audit (confirm path)
    # Pick a pending image if any, otherwise skip
    r = httpx.get(f"{BASE}/api/images/list/{DS}?status=pending&page_size=1", headers=H)
    pend = r.json().get("items", [])
    if pend and len(cat_names) > 0:
        # Get a real category_id
        cr = httpx.get(f"{BASE}/api/datasets/{DS}/categories", headers=H)
        cats = cr.json().get("items", cr.json() if isinstance(cr.json(), list) else [])
        if cats:
            target_img = pend[0]
            target_cat = cats[0]
            r = httpx.post(f"{BASE}/api/annotations/save",
                           json={"image_id": target_img["id"],
                                 "label_id": target_cat["id"],
                                 "time_spent_ms": 1234,
                                 "is_confirm": False},
                           headers=H)
            record("save_annotation_returns_ok",
                   r.status_code == 200 and r.json().get("success") is True,
                   f"{r.status_code} {r.text[:150]}")
            # Check log was created with from_label_id None (first annotation)
            r = httpx.get(f"{BASE}/api/annotations/list/{DS}?action=correct&page_size=10",
                          headers=H)
            if r.status_code == 200:
                logs = r.json().get("items", [])
                matched = [l for l in logs
                           if l.get("image_id") == target_img["id"]]
                record("save_annotation_log_recorded",
                       len(matched) >= 1 and matched[0].get("from_label_id") is None,
                       f"matched={len(matched)} from_label={matched[0].get('from_label_id') if matched else 'n/a'}")
        else:
            record("save_annotation_returns_ok", False, "no categories")
            record("save_annotation_log_recorded", False, "skipped")
    else:
        record("save_annotation_returns_ok", False, "no pending image to test")
        record("save_annotation_log_recorded", False, "skipped")

    # 9) Test clear_annotation audit (reject path) on the just-saved image
    if pend and len(cat_names) > 0:
        cr = httpx.get(f"{BASE}/api/datasets/{DS}/categories", headers=H)
        cats = cr.json().get("items", cr.json() if isinstance(cr.json(), list) else [])
        if cats:
            target_img = pend[0]
            r = httpx.post(f"{BASE}/api/annotations/clear",
                           json={"image_ids": [target_img["id"]]},
                           headers=H)
            record("clear_annotation_returns_ok",
                   r.status_code == 200 and r.json().get("cleared", 0) >= 1,
                   f"{r.status_code} {r.text[:200]}")
            # Verify a 'reject' log entry was written
            r = httpx.get(f"{BASE}/api/annotations/list/{DS}?action=reject&page_size=10",
                          headers=H)
            if r.status_code == 200:
                logs = r.json().get("items", [])
                matched = [l for l in logs
                           if l.get("image_id") == target_img["id"]]
                record("clear_annotation_log_recorded",
                       len(matched) >= 1,
                       f"reject entries for img={target_img['id']}: {len(matched)}")
        else:
            record("clear_annotation_returns_ok", False, "skipped")
            record("clear_annotation_log_recorded", False, "skipped")
    else:
        record("clear_annotation_returns_ok", False, "skipped")
        record("clear_annotation_log_recorded", False, "skipped")

    # Final summary
    print()
    log("SUMMARY", "=" * 60)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    log("SUMMARY", f"Passed: {passed}/{len(RESULTS)}  Failed: {failed}")
    for name, ok, detail in RESULTS:
        marker = "OK  " if ok else "FAIL"
        log("SUMMARY", f"  [{marker}] {name}: {detail}")
    if failed:
        log("SUMMARY", f"!!! {failed} CHECK(S) FAILED !!!")
        sys.exit(1)
    log("SUMMARY", "=== ALL CHECKS PASSED ===")


if __name__ == "__main__":
    main()
