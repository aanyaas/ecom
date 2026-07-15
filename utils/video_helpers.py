import os
import cv2
from PIL import Image

def allowed_video_file(filename):
    """Check if the filename has an allowed video extension."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in {'mp4', 'webm', 'avi', 'mov', 'mkv', 'flv', 'wmv'}

def convert_video_to_webp(video_path, output_webp_path, max_width=800, target_fps=16, max_duration_sec=15, quality=85):
    """
    Converts an input video file to a high-quality compressed animated WebP image.
    Resizes each frame to keep resolutions reasonable and downsamples frames to optimize size.
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError("Could not open video file.")

    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    if fps <= 0 or fps > 120:
        fps = 25

    # Determine frames to skip to match target FPS
    step = max(1, round(fps / target_fps))
    duration_per_frame_ms = int((1000 * step) / fps)

    max_frames = int(target_fps * max_duration_sec)

    frames = []
    count = 0
    saved_frames_count = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if count % step == 0:
            # Convert color space from BGR (OpenCV) to RGB (Pillow)
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb)

            # Resize the image to fit max_width
            w, h = img.size
            if w > max_width:
                h = int(h * (max_width / w))
                w = max_width
                img = img.resize((w, h), Image.Resampling.LANCZOS)

            frames.append(img)
            saved_frames_count += 1
            if saved_frames_count >= max_frames:
                break

        count += 1

    cap.release()

    if not frames:
        raise ValueError("No frames could be extracted from the video.")

    # Save frames as an animated WebP file
    frames[0].save(
        output_webp_path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_per_frame_ms,
        loop=0,
        quality=quality,
        method=4
    )
