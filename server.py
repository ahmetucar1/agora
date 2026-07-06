import cgi
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import unicodedata
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
STORAGE_ROOT = Path(os.environ.get("AGORA_STORAGE_DIR", str(ROOT))).resolve()
DATA_PATH = STORAGE_ROOT / "data" / "products.json"
DEFAULT_DATA_PATH = ROOT / "data" / "products.json"
ADMIN_DIR = ROOT / "admin"
IMAGES_DIR = STORAGE_ROOT / "images"
DEFAULT_IMAGES_DIR = ROOT / "images"
IMAGE_CACHE_DIR = ROOT / ".cache" / "images"
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MIN_RESIZE_WIDTH = 64
MAX_RESIZE_WIDTH = 1920


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def make_product_id(name: str, category_name: str = "") -> str:
    base = slugify(name) or "urun"
    if category_name:
        base = f"{slugify(category_name)}-{base}"
    return f"{base}-{uuid.uuid4().hex[:6]}"


class AgoraHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query_params = parse_qs(parsed.query)

        if path == "/api/products":
            self._send_json(self._load_products())
            return

        if path in {"/admin", "/admin/"}:
            self._send_file(ADMIN_DIR / "index.html")
            return

        if path.startswith("/admin/"):
            relative = path[len("/admin/"):]
            target = ADMIN_DIR / relative
            if target.is_file():
                self._send_file(target)
                return

        if path in {"/", "/index.html"}:
            self._send_file(ROOT / "index.html")
            return

        # Serve optimized image variants when a width is requested (e.g. ?w=360).
        if path.startswith("/images/"):
            resolved = self._resolve_image_path(path)
            if resolved is None:
                self._send_text("Not found", HTTPStatus.NOT_FOUND)
                return

            requested_width = self._requested_width(query_params)
            if requested_width is None:
                self._send_file(resolved)
                return

            self._send_resized_image(resolved, requested_width)
            return

        target = ROOT / path.lstrip("/")
        if target.is_file():
            self._send_file(target)
            return

        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/products/import":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            try:
                payload = json.loads(body or "{}")
            except json.JSONDecodeError:
                self._send_json({"ok": False, "error": "Geçersiz JSON"}, status=400)
                return

            categories = payload.get("categories")
            if not isinstance(categories, list):
                self._send_json({"ok": False, "error": "categories listesi bekleniyor"}, status=400)
                return

            self._write_products(categories)
            self._send_json({"ok": True, "message": "Ürünler başarıyla yüklendi", "categories": categories})
            return

        if path == "/api/products/add":
            self._handle_add_product()
            return

        if path == "/api/products/update":
            self._handle_update_product()
            return

        if path == "/api/products/delete":
            self._handle_delete_product()
            return

        if path == "/api/categories/delete":
            self._handle_delete_category()
            return

        self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def log_message(self, format, *args):
        return

    def _handle_add_product(self):
        ctype, _ = cgi.parse_header(self.headers.get("Content-Type", ""))
        if ctype != "multipart/form-data":
            self._send_json({"ok": False, "error": "multipart/form-data bekleniyor"}, status=400)
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers["Content-Type"]},
        )
        name = (form.getvalue("name") or "").strip()
        category_mode = (form.getvalue("category_mode") or "existing").strip()
        category_name = (form.getvalue("category") or "").strip()
        new_category_name = (form.getvalue("new_category_name") or "").strip()
        price_text = (form.getvalue("price") or "").strip()
        photo_item = form["photo"] if "photo" in form else None
        category_photo_item = form["category_photo"] if "category_photo" in form else None
        if isinstance(photo_item, list):
            photo_item = None
        if isinstance(category_photo_item, list):
            category_photo_item = None

        selected_category_name = new_category_name if category_mode == "new" else category_name

        if not name or not selected_category_name or not price_text:
            self._send_json({"ok": False, "error": "Ürün adı, kategori ve fiyat zorunlu"}, status=400)
            return

        try:
            price = float(price_text.replace(",", "."))
        except ValueError:
            self._send_json({"ok": False, "error": "Fiyat geçerli bir sayı olmalı"}, status=400)
            return

        image_name = None
        if photo_item is not None:
            filename = getattr(photo_item, "filename", None)
            if filename:
                try:
                    image_name = self._save_upload(photo_item)
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return

        category_image_name = None
        if category_mode == "new" and category_photo_item is not None:
            filename = getattr(category_photo_item, "filename", None)
            if filename:
                try:
                    category_image_name = self._save_upload(category_photo_item)
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return

        data = self._load_products()
        categories = data.get("categories", [])

        category = next((item for item in categories if item.get("name", "").lower() == selected_category_name.lower()), None)
        if category is None:
            category = {
                "id": slugify(selected_category_name) or "genel",
                "name": selected_category_name,
                "image": category_image_name or self._fallback_category_image(selected_category_name),
                "products": [],
            }
            categories.append(category)

        product_id = make_product_id(name, selected_category_name)
        category.setdefault("products", []).append({
            "id": product_id,
            "name": name,
            "price": price,
            "image": image_name,
        })

        self._write_products(categories)
        self._send_json({
            "ok": True,
            "message": "Ürün başarıyla eklendi",
            "product": {"id": product_id, "name": name, "category": selected_category_name, "price": price, "image": image_name},
        })

    def _handle_update_product(self):
        ctype, _ = cgi.parse_header(self.headers.get("Content-Type", ""))
        if ctype != "multipart/form-data":
            self._send_json({"ok": False, "error": "multipart/form-data bekleniyor"}, status=400)
            return

        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers["Content-Type"]},
        )
        product_id = (form.getvalue("product_id") or "").strip()
        name = (form.getvalue("name") or "").strip()
        category_mode = (form.getvalue("category_mode") or "existing").strip()
        category_name = (form.getvalue("category") or "").strip()
        new_category_name = (form.getvalue("new_category_name") or "").strip()
        price_text = (form.getvalue("price") or "").strip()
        photo_item = form["photo"] if "photo" in form else None
        category_photo_item = form["category_photo"] if "category_photo" in form else None
        if isinstance(photo_item, list):
            photo_item = None
        if isinstance(category_photo_item, list):
            category_photo_item = None

        selected_category_name = new_category_name if category_mode == "new" else category_name

        if not product_id or not name or not selected_category_name or not price_text:
            self._send_json({"ok": False, "error": "Ürün ID, adı, kategori ve fiyat zorunlu"}, status=400)
            return

        try:
            price = float(price_text.replace(",", "."))
        except ValueError:
            self._send_json({"ok": False, "error": "Fiyat geçerli bir sayı olmalı"}, status=400)
            return

        data = self._load_products()
        categories = data.get("categories", [])
        source_category = None
        product_entry = None

        for category in categories:
            products = category.get("products", [])
            for candidate in products:
                if candidate.get("id") == product_id:
                    source_category = category
                    product_entry = candidate
                    break
            if product_entry is not None:
                break

        if product_entry is None:
            self._send_json({"ok": False, "error": "Ürün bulunamadı"}, status=404)
            return

        image_name = product_entry.get("image")
        if photo_item is not None:
            filename = getattr(photo_item, "filename", None)
            if filename:
                try:
                    image_name = self._save_upload(photo_item)
                except ValueError as exc:
                    self._send_json({"ok": False, "error": str(exc)}, status=400)
                    return

        if source_category is not None:
            source_category["products"] = [
                item for item in source_category.get("products", []) if item.get("id") != product_id
            ]

        target_category = next((item for item in categories if item.get("name", "").lower() == selected_category_name.lower()), None)
        if target_category is None:
            category_image_name = None
            if category_mode == "new" and category_photo_item is not None:
                filename = getattr(category_photo_item, "filename", None)
                if filename:
                    try:
                        category_image_name = self._save_upload(category_photo_item)
                    except ValueError as exc:
                        self._send_json({"ok": False, "error": str(exc)}, status=400)
                        return

            target_category = {
                "id": slugify(selected_category_name) or "genel",
                "name": selected_category_name,
                "image": category_image_name or self._fallback_category_image(selected_category_name),
                "products": [],
            }
            categories.append(target_category)

        target_category.setdefault("products", []).append({
            "id": product_id,
            "name": name,
            "price": price,
            "image": image_name,
        })

        self._write_products(categories)
        self._send_json({
            "ok": True,
            "message": "Ürün güncellendi",
            "product": {"id": product_id, "name": name, "category": selected_category_name, "price": price, "image": image_name},
        })

    def _handle_delete_product(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "Geçersiz JSON"}, status=400)
            return

        product_id = payload.get("id")
        if not product_id:
            self._send_json({"ok": False, "error": "Ürün ID zorunlu"}, status=400)
            return

        data = self._load_products()
        categories = data.get("categories", [])
        removed = False

        for category in categories:
            products = category.get("products", [])
            filtered = [item for item in products if item.get("id") != product_id]
            if len(filtered) != len(products):
                category["products"] = filtered
                removed = True
                break

        if not removed:
            self._send_json({"ok": False, "error": "Ürün bulunamadı"}, status=404)
            return

        self._write_products(categories)
        self._send_json({"ok": True, "message": "Ürün silindi"})

    def _handle_delete_category(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            self._send_json({"ok": False, "error": "Geçersiz JSON"}, status=400)
            return

        category_name = (payload.get("name") or payload.get("id") or "").strip()
        if not category_name:
            self._send_json({"ok": False, "error": "Kategori adı zorunlu"}, status=400)
            return

        data = self._load_products()
        categories = data.get("categories", [])
        filtered = [item for item in categories if item.get("name", "").lower() != category_name.lower()]
        if len(filtered) == len(categories):
            self._send_json({"ok": False, "error": "Kategori bulunamadı"}, status=404)
            return

        self._write_products(filtered)
        self._send_json({"ok": True, "message": "Kategori silindi"})

    def _save_upload(self, upload) -> Optional[str]:
        filename = getattr(upload, "filename", "") or ""
        if not filename:
            return None

        ext = Path(filename).suffix.lower() or ".jpg"
        stem = slugify(Path(filename).stem) or "image"
        binary = upload.file.read()

        if ext in {".heic", ".heif"}:
            return self._save_heic_as_jpeg(stem, binary)

        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError("Desteklenmeyen görsel formatı. JPG, PNG, WEBP veya GIF yükleyin.")

        target = self._next_image_path(stem, ext)

        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            handle.write(binary)
        return target.name

    def _next_image_path(self, stem: str, ext: str) -> Path:
        target = IMAGES_DIR / f"{stem}{ext}"
        counter = 1
        while target.exists():
            target = IMAGES_DIR / f"{stem}-{counter}{ext}"
            counter += 1
        return target

    def _save_heic_as_jpeg(self, stem: str, binary: bytes) -> str:
        target = self._next_image_path(stem, ".jpg")
        target.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".heic") as temp_input:
            temp_input.write(binary)
            source_path = Path(temp_input.name)

        try:
            result = subprocess.run(
                ["sips", "-s", "format", "jpeg", str(source_path), "--out", str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            if result.returncode != 0:
                raise ValueError("HEIC görseli dönüştürülemedi. Lütfen JPG/PNG yükleyin.")
        finally:
            try:
                source_path.unlink()
            except OSError:
                pass

        return target.name

    def _fallback_category_image(self, category_name: str) -> str:
        lowered = category_name.lower()
        if "kahve" in lowered:
            return "kahveler.jpg"
        if "milk" in lowered:
            return "milkshake.jpg"
        if "frozen" in lowered:
            return "frozen.jpg"
        if "iced" in lowered:
            return "icedcoffe.jpg"
        if "kokteyl" in lowered:
            return "kokteyl.jpg"
        if "dondurma" in lowered:
            return "dondurma.jpg"
        return "sıcakicecek.jpg"

    def _normalize_products_payload(self, data):
        categories = data.get("categories", []) if isinstance(data, dict) else []
        if not isinstance(categories, list):
            categories = []

        normalized = []
        for category_index, category in enumerate(categories):
            if not isinstance(category, dict):
                continue

            category_name = category.get("name") or f"Kategori {category_index + 1}"
            category_id = category.get("id") or slugify(category_name) or f"kategori-{category_index + 1}"
            products = category.get("products", [])
            if not isinstance(products, list):
                products = []

            normalized_products = []
            for product_index, product in enumerate(products):
                if not isinstance(product, dict):
                    continue
                normalized_product = dict(product)
                normalized_product["id"] = normalized_product.get("id") or make_product_id(
                    normalized_product.get("name") or f"urun-{product_index + 1}", category_name
                )
                normalized_products.append(normalized_product)

            normalized.append({
                **category,
                "id": category_id,
                "name": category_name,
                "products": normalized_products,
            })

        return {"categories": normalized}

    def _load_products(self):
        source_path = DATA_PATH if DATA_PATH.exists() else DEFAULT_DATA_PATH
        if source_path.exists():
            try:
                payload = json.loads(source_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {"categories": []}
            return self._normalize_products_payload(payload)
        return {"categories": []}

    def _write_products(self, categories):
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = self._normalize_products_payload({"categories": categories})
        DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _send_json(self, data, status=HTTPStatus.OK):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_text(self, text, status=HTTPStatus.OK):
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _send_file(self, path: Path):
        if not path.exists():
            self._send_text("Not found", HTTPStatus.NOT_FOUND)
            return

        content_type, _ = mimetypes.guess_type(str(path))
        if content_type is None:
            content_type = "application/octet-stream"

        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            return

    def _resolve_image_path(self, request_path: str) -> Optional[Path]:
        image_name = Path(request_path).name
        if not image_name:
            return None

        image_dirs = [IMAGES_DIR]
        if DEFAULT_IMAGES_DIR != IMAGES_DIR:
            image_dirs.append(DEFAULT_IMAGES_DIR)

        requested = unicodedata.normalize("NFC", image_name).casefold()
        for images_dir in image_dirs:
            if not images_dir.exists():
                continue
            for candidate in images_dir.iterdir():
                if not candidate.is_file():
                    continue
                normalized = unicodedata.normalize("NFC", candidate.name).casefold()
                if normalized == requested:
                    return candidate
        return None

    def _requested_width(self, query_params) -> Optional[int]:
        values = query_params.get("w") or []
        if not values:
            return None
        try:
            requested = int(values[0])
        except (TypeError, ValueError):
            return None
        return max(MIN_RESIZE_WIDTH, min(MAX_RESIZE_WIDTH, requested))

    def _send_resized_image(self, source_path: Path, width: int):
        ext = source_path.suffix.lower()
        if ext not in SUPPORTED_IMAGE_EXTENSIONS:
            self._send_file(source_path)
            return

        cached_path = self._cached_resized_path(source_path, width)
        if not cached_path.exists():
            cached_path.parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["sips", "-Z", str(width), str(source_path), "--out", str(cached_path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode != 0 or not cached_path.exists():
                self._send_file(source_path)
                return

        self._send_file(cached_path)

    def _cached_resized_path(self, source_path: Path, width: int) -> Path:
        stat = source_path.stat()
        key_source = f"{source_path.resolve()}:{stat.st_mtime_ns}:{stat.st_size}:{width}"
        digest = hashlib.sha1(key_source.encode("utf-8")).hexdigest()
        ext = source_path.suffix.lower() or ".jpg"
        return IMAGE_CACHE_DIR / f"{digest}{ext}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), AgoraHandler)
    print(f"Agora server running on http://127.0.0.1:{port}")
    server.serve_forever()
