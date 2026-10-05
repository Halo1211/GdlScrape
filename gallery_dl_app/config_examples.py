"""Small starter configs, with their purpose and source visible in the GUI."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfigExample:
    key: str
    title: tuple[str, str]
    description: tuple[str, str]
    source: str

    @property
    def path(self) -> Path:
        return Path(__file__).parent / "assets" / "config-examples" / (self.key + ".json")


OFFICIAL_EXAMPLE = "https://github.com/mikf/gallery-dl/blob/v1.32.14/docs/gallery-dl-example.conf"
EXAMPLES = (
    ConfigExample("pixiv-animation-archive", ("Pixiv: original animation frames in ZIP", "Pixiv: frame animasi asli di ZIP"),
                  ("Stores original Ugoira frames and animation.json in ZIP while retaining downloaded frames. Does not encode video or require FFmpeg. Requires your Pixiv login. Shared action options keep originals.", "Menyimpan frame Ugoira asli dan animation.json di ZIP sambil mempertahankan frame unduhan. Tidak mengonversi video atau memerlukan FFmpeg. Memerlukan login Pixiv. Opsi tindakan bersama mempertahankan file asli."),
                  "https://github.com/mikf/gallery-dl/discussions/6147"),
    ConfigExample("instagram-selected-media", ("Instagram: posts, Reels and Highlights", "Instagram: postingan, Reels dan Sorotan"),
                  ("Profile links collect posts, Reels and Highlights with request pauses. Stories and tagged posts are excluded from profile traversal. Set up your own login; availability depends on the account. Direct story URLs still select stories.", "Tautan profil mengambil postingan, Reels dan Sorotan dengan jeda request. Story dan postingan bertanda tidak diambil lewat profil. Atur login sendiri; akses mengikuti akun. URL story langsung tetap memilih story."),
                  "https://github.com/mikf/gallery-dl/discussions/5586"),
    ConfigExample("reddit-connected-media", ("Reddit: linked media in the post folder", "Reddit: media tertaut dalam folder postingan"),
                  ("Follows Imgur and Redgifs links from Reddit into the Reddit post folder. Child filenames retain the Reddit post ID. Direct Imgur/Redgifs downloads keep their own defaults. Configure Reddit login if required.", "Mengikuti tautan Imgur dan Redgifs dari Reddit ke folder postingan Reddit. Nama file anak menyertakan ID postingan Reddit. Unduhan Imgur/Redgifs langsung tetap memakai default sendiri. Atur login Reddit bila diperlukan."),
                  "https://github.com/mikf/gallery-dl/issues/7721"),
    ConfigExample("windows-short-paths", ("ArtStation: shorter Windows paths", "ArtStation: path Windows lebih pendek"),
                  ("Windows-compatible path handling, with ArtStation project titles limited to 60 characters and filenames based on project/asset IDs. Keeps separate assets distinct. Choose a short download root for long-path-sensitive programs.", "Penanganan path kompatibel Windows, judul proyek ArtStation dibatasi 60 karakter dan nama file berdasarkan ID proyek/aset. Aset berbeda tetap terpisah. Pilih folder utama pendek untuk program yang sensitif terhadap path panjang."),
                  "https://github.com/mikf/gallery-dl/discussions/5307"),
    ConfigExample("tumblr-original-posts", ("Tumblr: original photos and videos", "Tumblr: foto dan video postingan asli"),
                  ("Collects photo and video posts, includes inline media and skips reblogs/external websites. These rules also apply to Likes unless overridden for that page type. Video downloads may require yt-dlp depending on the source.", "Mengambil postingan foto/video beserta media inline, melewati reblog dan situs eksternal. Aturan ini juga berlaku pada Likes kecuali ditimpa untuk jenis halaman tersebut. Video dapat memerlukan yt-dlp sesuai sumber."), OFFICIAL_EXAMPLE),
    ConfigExample("manga-cbz-information", ("MangaDex: CBZ with information inside", "MangaDex: CBZ berisi informasi"),
                  ("Creates info.json at the start of each chapter and includes it with the images inside CBZ. Keeps original files. Edit extra archive files through After downloading.", "Membuat info.json saat setiap bab dimulai dan memasukkannya bersama gambar ke CBZ. File asli tetap ada. Edit file tambahan arsip melalui Setelah Mengunduh."),
                  "https://github.com/mikf/gallery-dl/discussions/2872"),
    ConfigExample("pixiv-consistent-history", ("Pixiv: shared history for profile and search", "Pixiv: riwayat yang sama untuk profil dan pencarian"),
                  ("Uses one history file and the same archive ID for Pixiv artwork from profiles and searches, even in different folders. Choose a history location before downloading. Requires Pixiv login. Existing history records are not rewritten.", "Memakai satu file riwayat dan ID arsip yang sama untuk karya Pixiv dari profil dan pencarian, walau folder berbeda. Pilih lokasi riwayat sebelum mengunduh. Memerlukan login Pixiv. Catatan riwayat lama tidak ditulis ulang."),
                  "https://github.com/mikf/gallery-dl/discussions/7036"),
    ConfigExample("metadata-per-site", ("Information files per website (JSON + post text)", "File informasi per situs (JSON + teks postingan)"),
                  ("Pixiv and Instagram get JSON; Twitter gets JSON and post text. Original downloads stay intact. Choose a folder and set up login if needed.", "Pixiv dan Instagram mendapat JSON; Twitter mendapat JSON dan teks postingan. Unduhan asli tetap utuh. Pilih folder dan atur login bila diperlukan."), OFFICIAL_EXAMPLE),
    ConfigExample("booru-tags", ("Tag text files for booru websites", "File tag teks untuk situs booru"),
                  ("Writes one tag per line beside each booru image. Restricted to Danbooru, e621, Safebooru and Rule34; other websites keep their usual behavior.", "Menulis satu tag per baris di samping gambar booru. Berlaku untuk Danbooru, e621, Safebooru dan Rule34; situs lain tetap memakai perilaku biasa."), OFFICIAL_EXAMPLE),
    ConfigExample("manga-cbz", ("MangaDex chapters as CBZ (keep originals)", "Bab MangaDex ke CBZ (file asli tetap ada)"),
                  ("Downloads English MangaDex chapters, then creates a CBZ for a comic reader. Downloaded chapter images are also kept.", "Mengunduh bab MangaDex berbahasa Inggris, lalu membuat CBZ untuk pembaca komik. Gambar bab yang diunduh tetap disimpan."), OFFICIAL_EXAMPLE),
    ConfigExample("social-images", ("Social websites: images, history and request pauses", "Situs sosial: gambar, riwayat dan jeda permintaan"),
                  ("Instagram, Twitter and Bluesky: keep image files, use separate history files and pause between requests. GIF is included; videos are excluded by file extension. Pauses do not guarantee access or prevent rate limits.", "Instagram, Twitter dan Bluesky: simpan gambar, gunakan riwayat terpisah dan jeda antarpermintaan. GIF termasuk; video disaring lewat ekstensi. Jeda tidak menjamin akses atau mencegah pembatasan situs."),
                  "https://gdl-org.github.io/docs/configuration.html#extractor-file-filter"),
    ConfigExample("pixiv-animation", ("Pixiv animations as MP4 (keep original frames)", "Animasi Pixiv ke MP4 (frame asli tetap ada)"),
                  ("Static artwork stays unchanged. Ugoira animations are converted to MP4 while keeping source files. Requires FFmpeg, plus your Pixiv login. Choose After downloading to change the format.", "Karya statis tetap seperti semula. Animasi Ugoira dikonversi ke MP4 dan file sumber tetap disimpan. Memerlukan FFmpeg dan login Pixiv. Pilih Setelah Mengunduh untuk mengganti format."),
                  "https://github.com/mikf/gallery-dl/discussions/6147"),
    ConfigExample("imgur-folders", ("Imgur: folders based on post text", "Imgur: folder berdasarkan teks postingan"),
                  ("An example of a condition: posts containing 'nature' go to Nature; the others go to Other. Uses only Imgur settings. Change the keyword/folders in the directory editor; fields differ between websites.", "Contoh kondisi: postingan berisi 'nature' masuk ke Nature; sisanya ke Other. Hanya mengubah Imgur. Ganti kata/folder di editor folder; field tiap situs bisa berbeda."),
                  "https://github.com/mikf/gallery-dl/discussions/4703"),
    ConfigExample("separate-information", ("Danbooru: separate media and JSON folders", "Danbooru: folder media dan JSON terpisah"),
                  ("Media goes to images/danbooru, JSON to metadata/danbooru under the chosen download root. JSON uses tab indentation. Change the information location through After downloading.", "Media masuk ke images/danbooru, JSON ke metadata/danbooru di bawah folder utama unduhan. JSON memakai indentasi tab. Ubah lokasi informasi lewat Setelah Mengunduh."),
                  "https://github.com/mikf/gallery-dl/discussions/6094"),
    ConfigExample("social-post-text", ("Twitter and Tumblr: post text in separate folders", "Twitter dan Tumblr: teks postingan di folder terpisah"),
                  ("Writes text once per post. Tumblr tries the fields used by different post types; Twitter uses content. No login data is included. Field availability and text-only posts depend on the website/extractor.", "Menulis teks sekali per postingan. Tumblr mencoba field dari beberapa jenis postingan; Twitter memakai content. Tidak menyertakan data login. Field dan postingan tanpa media mengikuti situs/extractor."),
                  "https://github.com/mikf/gallery-dl/discussions/5628"),
)
