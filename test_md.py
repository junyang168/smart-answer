import re
import markdown

original_md = """> **馬太福音 12:1-14**
>
> 那時，耶穌在安息日從麥地經過。他的門徒餓了.....
>
> 耶穌對他們說....."""

def preserve_blockquote_lines(match):
    line = match.group(0)
    return line.rstrip() + "  "

processed_md = re.sub(r'^\s*>.*$', preserve_blockquote_lines, original_md, flags=re.MULTILINE)

print("--- Original ---")
print(markdown.markdown(original_md, extensions=['tables', 'footnotes']))

print("\n--- Processed ---")
print(markdown.markdown(processed_md, extensions=['tables', 'footnotes']))
