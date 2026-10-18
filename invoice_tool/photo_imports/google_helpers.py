"""Legacy-compatible google_photos helpers; explicit runtime dependencies."""

def google_photos_page_html(share_url, *, HTTPError, Request, URLError, urlopen, urlsplit):
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PrasinosPowerInvoiceTool/1.0)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with urlopen(Request(share_url, headers=request_headers), timeout=30) as response:
            final_host = urlsplit(response.geturl()).netloc.lower().split(":")[0]
            if final_host == "accounts.google.com":
                raise ValueError("这个 Google Photos 分享链接需要登录或没有公开访问权限，服务器无法直接导入。")
            content_type = response.headers.get_content_type()
            if content_type and "html" not in content_type:
                raise ValueError("链接返回的不是 Google Photos 分享页面。")
            return response.read(12 * 1024 * 1024).decode("utf-8", errors="ignore")
    except HTTPError as error:
        if error.code in {401, 403}:
            raise ValueError("这个 Google Photos 分享链接需要登录或没有访问权限，服务器无法直接导入。") from error
        raise ValueError(f"读取 Google Photos 分享链接失败：HTTP {error.code}") from error
    except URLError as error:
        raise ValueError(f"读取 Google Photos 分享链接失败：{error.reason}") from error


def normalize_embedded_google_url(value, *, html):
    text = html.unescape(value or "")
    replacements = {
        "\\/": "/",
        "\\u003d": "=",
        "\\u0026": "&",
        "\\u003f": "?",
        "\\u0025": "%",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def google_image_score(url, *, re):
    size_match = re.search(r"=w(\d+)-h(\d+)", url)
    if size_match:
        return int(size_match.group(1)) * int(size_match.group(2))
    square_match = re.search(r"=s(\d+)", url)
    if square_match:
        size = int(square_match.group(1))
        return size * size
    return len(url)


def google_image_dimensions(url, *, re):
    size_match = re.search(r"=w(\d+)-h(\d+)", url)
    if size_match:
        return int(size_match.group(1)), int(size_match.group(2))
    square_match = re.search(r"=s(\d+)", url)
    if square_match:
        size = int(square_match.group(1))
        return size, size
    return None


def is_likely_google_account_image(url, *, google_image_dimensions, urlsplit):
    parsed = urlsplit(url)
    lower = url.casefold()
    account_keywords = (
        "avatar",
        "profile",
        "userphoto",
        "photo.jpg",
        "account",
        "person",
        "people",
        "anonymous",
        "googleusercontent.com/a/",
    )
    if any(keyword in lower for keyword in account_keywords):
        return True
    dimensions = google_image_dimensions(url)
    if dimensions:
        width, height = dimensions
        if width <= 300 and height <= 300:
            return True
    return False


def google_photo_download_candidates(url, *, google_photo_url_key):
    base = google_photo_url_key(url)
    candidates = [
        f"{base}=d",
        f"{base}=s0",
        f"{base}=s0-d",
        url,
    ]
    return list(dict.fromkeys(candidates))


def extract_google_photo_urls(page_html, *, GOOGLE_PHOTOS_MAX_IMPORT, google_image_score, google_photo_url_key, is_likely_google_account_image, normalize_embedded_google_url, re):
    normalized = normalize_embedded_google_url(page_html)
    matches = re.findall(r"https://lh3\.googleusercontent\.com/[^\s\"'<>\\\])]+", normalized)
    by_photo = {}
    for raw_url in matches:
        url = raw_url.rstrip(".,;")
        if is_likely_google_account_image(url):
            continue
        photo_key = google_photo_url_key(url)
        if not photo_key:
            continue
        if photo_key not in by_photo or google_image_score(url) > google_image_score(by_photo[photo_key]):
            by_photo[photo_key] = url
    urls = list(by_photo.values())
    return urls[:GOOGLE_PHOTOS_MAX_IMPORT], len(urls)


def downloaded_image_extension(content_type, url, *, ALLOWED_IMAGE_EXTENSIONS, Path, urlsplit):
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/heic": ".heic",
        "image/heif": ".heif",
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix.lstrip(".") in ALLOWED_IMAGE_EXTENSIONS:
        return suffix
    return ".jpg"


def unique_download_path(folder, filename, *, secure_filename):
    candidate = folder / secure_filename(filename)
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        next_candidate = candidate.with_name(f"{candidate.stem}-{counter}{candidate.suffix}")
        if not next_candidate.exists():
            return next_candidate
        counter += 1


def download_google_photo_image(url, target_dir, index, *, GOOGLE_PHOTOS_MAX_IMAGE_BYTES, HTTPError, Request, URLError, downloaded_image_extension, google_photo_download_candidates, hashlib, unique_download_path, urlopen):
    request_headers = {
        "User-Agent": "Mozilla/5.0 (compatible; PrasinosPowerInvoiceTool/1.0)",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    last_error = None
    for candidate_url in google_photo_download_candidates(url):
        try:
            with urlopen(Request(candidate_url, headers=request_headers), timeout=45) as response:
                content_type = response.headers.get_content_type()
                if not (content_type or "").startswith("image/"):
                    raise ValueError(f"不是图片内容：{content_type or 'unknown'}")
                extension = downloaded_image_extension(content_type, candidate_url)
                target_path = unique_download_path(target_dir, f"google-photo-{index:03d}{extension}")
                total = 0
                digest = hashlib.sha256()
                with open(target_path, "wb") as file:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > GOOGLE_PHOTOS_MAX_IMAGE_BYTES:
                            file.close()
                            try:
                                target_path.unlink()
                            except FileNotFoundError:
                                pass
                            raise ValueError("图片超过 30MB，已跳过。")
                        digest.update(chunk)
                        file.write(chunk)
                return target_path, digest.hexdigest(), total
        except ValueError as error:
            if "超过 30MB" in str(error):
                raise
            last_error = error
        except (HTTPError, URLError, OSError) as error:
            last_error = error
    raise ValueError(f"无法下载原图：{last_error}")


def import_google_photos_share_to_incoming(order, share_url, *, download_google_photo_image, extract_google_photo_urls, google_photos_page_html, is_google_photos_share_url, service_order_incoming_folder):
    if not is_google_photos_share_url(share_url):
        raise ValueError("请输入 Google Photos 分享链接。")
    page_html = google_photos_page_html(share_url)
    image_urls, total_found = extract_google_photo_urls(page_html)
    if not image_urls:
        raise ValueError("没有从这个分享链接中识别到可下载照片。链接可能需要登录，或 Google 页面结构发生变化。")
    target_dir = service_order_incoming_folder(order["order_number"], "google-photos")
    imported = []
    skipped = []
    seen_hashes = set()
    for index, image_url in enumerate(image_urls, start=1):
        try:
            path, digest, size = download_google_photo_image(image_url, target_dir, index)
            if digest in seen_hashes:
                path.unlink(missing_ok=True)
                skipped.append(f"第 {index} 张：重复照片")
                continue
            seen_hashes.add(digest)
            imported.append({"path": path, "size": size})
        except Exception as error:
            skipped.append(f"第 {index} 张：{error}")
    if not imported and skipped:
        raise ValueError("照片下载失败：" + "；".join(skipped[:5]))
    truncated_count = max(0, total_found - len(image_urls))
    return imported, skipped, target_dir, total_found, truncated_count


def send_google_photos_import_result(user, order, share_url, imported, skipped, target_dir, total_found=0, truncated_count=0, error=None, *, GOOGLE_PHOTOS_MAX_IMPORT, app, create_message, html, send_email):
    success_count = len(imported or [])
    skipped_count = len(skipped or [])
    if error:
        title = f"Google Photos 导入失败：{order['order_number']}"
        lines = [
            f"工单：{order['order_number']}",
            f"链接：{share_url}",
            f"失败原因：{error}",
        ]
    else:
        title = f"Google Photos 导入完成：{order['order_number']}"
        lines = [
            f"工单：{order['order_number']}",
            f"链接：{share_url}",
            f"识别到照片：{total_found} 张",
            f"尝试导入：{success_count + skipped_count} 张",
            f"导入照片：{success_count} 张",
            f"跳过/失败：{skipped_count} 张",
            f"保存位置：{target_dir}",
            "照片已放入 incoming/google-photos，photo_worker 会自动处理到 pictures。",
        ]
        if truncated_count:
            lines.append(f"注意：识别到的照片超过系统单次导入上限 {GOOGLE_PHOTOS_MAX_IMPORT} 张，仍有 {truncated_count} 张未导入。")
        if skipped:
            lines.append("前几条跳过/失败信息：")
            lines.extend(skipped[:10])
    body = "\n".join(lines)
    create_message(user["id"], title, body, f"/service-orders/{order['id']}")
    email = (user["email"] or "").strip()
    if email:
        try:
            send_email(
                to=email,
                subject=title,
                html=f"<p>{html.escape(body).replace(chr(10), '<br>')}</p>",
            )
        except Exception:
            app.logger.exception("Unable to send Google Photos import result email to %s", email)


def run_google_photos_import_job(order_id, user_id, share_url, *, app, db, import_google_photos_share_to_incoming, send_google_photos_import_result):
    with app.app_context():
        order = db().execute("select * from service_orders where id = ?", (order_id,)).fetchone()
        user = db().execute("select * from users where id = ?", (user_id,)).fetchone()
        if not order or not user:
            return
        try:
            imported, skipped, target_dir, total_found, truncated_count = import_google_photos_share_to_incoming(order, share_url)
            send_google_photos_import_result(user, order, share_url, imported, skipped, target_dir, total_found, truncated_count)
            db().commit()
        except Exception as error:
            db().rollback()
            try:
                send_google_photos_import_result(user, order, share_url, [], [], "", error=str(error))
                db().commit()
            except Exception:
                db().rollback()
                app.logger.exception("Unable to notify Google Photos import result for order %s", order["order_number"])


def start_google_photos_import_job(order_id, user_id, share_url, *, run_google_photos_import_job, threading):
    thread = threading.Thread(
        target=run_google_photos_import_job,
        args=(order_id, user_id, share_url),
        daemon=True,
    )
    thread.start()
