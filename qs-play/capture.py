"""capture.py —— 读屏与帧差（阶段 1 的客观证据源）

为什么要有这个文件：A/B 实测证明，零反馈时模型会说"前两轮攻击已生效"而画面
根本没动过。所以"世界有没有变"不能由模型说，也不能由我猜，只能由像素说。
这里产出的 frame_changed 是**进 prompt 的事实**，也是**出 prompt 的裁决依据**。

零额外依赖：PIL（本机已装）+ 标准库。mss / pyautogui 都没装，不引入。
"""
import base64
import io
import time

from PIL import Image, ImageChops


def grab():
    """抓全屏，返回 PIL.Image（RGB）。"""
    return Image.grab() if hasattr(Image, "grab") else _grab_windows()


def _grab_windows():
    from PIL import ImageGrab          # Windows 路径
    return ImageGrab.grab(all_screens=False).convert("RGB")


def resize_to(img, width=1280):
    if img.width <= width:
        return img
    h = int(img.height * width / img.width)
    return img.resize((width, h), Image.LANCZOS)


def to_data_url(img, quality=82):
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


def frame_diff(a, b, grid=(16, 9)):
    """粗网格帧差：返回 (变化格占比, 平均像素强度差)。

    ⚠ 10-23 修过一个会污染判断的实现错：上一版每格只取 8 个像素却按 144 格消费，
    等于只量了画面**顶部 12%**——转镜头（动地平线）报高分，往前走（地平线不动）
    报 0.0000，而整幅分块重算是 0.95。现在按真实 8x8 块取均值，覆盖全图。

    阈值：单块 8x8 灰度差均值 >6 记为该块变化。宁可高估"动了"，
    也不放过"完全没动"——后者才是谎报检测要抓的东西。
    """
    if a is None or b is None:
        return 1.0, 1.0
    gw, gh = grid
    bw, bh = 8, 8                        # 每块 8x8 像素，整图 bw*gw x bh*gh
    sa = a.resize((bw * gw, bh * gh), Image.BILINEAR).convert("L")
    sb = b.resize((bw * gw, bh * gh), Image.BILINEAR).convert("L")
    px = list(ImageChops.difference(sa, sb).getdata())
    row = bw * gw
    mean = sum(px) / len(px)
    cells_changed = 0
    for gy in range(gh):
        base = gy * bh * row
        for gx in range(gw):
            s = 0
            for r in range(bh):
                off = base + r * row + gx * bw
                s += sum(px[off:off + bw])
            if s / float(bw * bh) > 6.0:
                cells_changed += 1
    return cells_changed / float(gw * gh), round(mean, 2)


def new_frame_pair():
    """连拍两帧给自检用（同一秒内，应当判为几乎不变）。"""
    a = grab()
    time.sleep(0.25)
    b = grab()
    return a, b
