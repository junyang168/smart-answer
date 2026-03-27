from opencc import OpenCC

from backend.sermon_to_video.core.subtitle import _align_segments_to_script
from backend.sermon_to_video.core.subtitle import _build_script_text
from backend.sermon_to_video.core.subtitle import _parse_whisper_srt


def test_subtitle_alignment_keeps_boundary_for_split_reference():
    cc = OpenCC("s2t")
    raw_srt = "\n\n".join(
        [
            "19\n00:00:40,000 --> 00:00:42,000\n在前面的马太福音11",
            "20\n00:00:42,000 --> 00:00:43,000\n25-27",
            "21\n00:00:43,000 --> 00:00:45,000\n耶稣已经说得很清楚",
            "22\n00:00:45,000 --> 00:00:48,000\n人不能靠自己的聪明通达认识神",
            "23\n00:00:48,000 --> 00:00:50,000\n只有子所愿意启示的",
            "24\n00:00:50,000 --> 00:00:52,000\n人才能认识父",
        ]
    )
    storyboard = {
        "scenes": [
            {
                "voiceover_text": "這不是一句脫離上下文的安慰話。"
                "在前面的馬太福音 11:25–27，耶穌已經說得很清楚："
                "人不能靠自己的「聰明通達」認識神，只有子所願意啟示的，"
                "人才能認識父。[s3_1]如果是這樣，人唯一的出路，就不是再靠自己努力一點，而是來到耶穌這裡。"
            }
        ]
    }

    segments = _parse_whisper_srt(raw_srt, cc)
    script_text = _build_script_text(storyboard, cc)
    aligned = _align_segments_to_script(segments, script_text)

    assert aligned[0]["caption_text"].endswith("11:")
    assert aligned[1]["caption_text"] == "25–27，"
    assert aligned[2]["caption_text"] == "耶穌已經說得很清楚："
    assert aligned[3]["caption_text"] == "人不能靠自己的「聰明通達」認識神，"
    assert aligned[4]["caption_text"] == "只有子所願意啟示的，"
    assert aligned[5]["caption_text"].startswith("人才能認識父。")


def test_build_script_text_removes_cue_markers():
    cc = OpenCC("s2t")
    storyboard = {
        "scenes": [
            {"voiceover_text": "第一句。[s7_1]第二句。"},
            {"voiceover_text": "[s7_2]第三句。"},
        ]
    }

    script_text = _build_script_text(storyboard, cc)

    assert "[s7_1]" not in script_text
    assert "[s7_2]" not in script_text
    assert script_text == "第一句。第二句。第三句。"


def test_build_script_text_removes_ssml_phoneme_tags_but_keeps_text():
    cc = OpenCC("s2t")
    storyboard = {
        "scenes": [
            {"voiceover_text": '我就使你們<phoneme alphabet="sapi" ph="de2">得</phoneme>安息。'},
        ]
    }

    script_text = _build_script_text(storyboard, cc)

    assert "<phoneme" not in script_text
    assert "</phoneme>" not in script_text
    assert script_text == "我就使你們得安息。"
