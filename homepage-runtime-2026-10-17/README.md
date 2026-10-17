# Homepage catalog runtime, 2026-10-17

This is the existing public gidvms.ru catalog payload from Tilda blocks 13
v308P3 and 14 v99P1, delivered as one immutable external classic defer script.
It is used by replacement blocks 13 v309 and 14 v100 on homepage 214257109.

The LZMA/Base85 payload is unchanged. It still decodes to 1,186,120 UTF-8 bytes.
The existing GVSDecodeLZ1 from block 10V-2 remains the decoder. Current other
blocks at preparation: 11 v102, 10B-2 v182, 10B-3 v7, 10V v163, 10V-2 v8 Fast.

Loading contract:

- Block 13 registers the original early DOMContentLoaded entry point and
  declares the parser-inserted external defer script.
- Block 14 preserves its prefix script and the gvs-catalog309-anchor marker.
- Defer executes after the entire document has parsed, before DOMContentLoaded.
- Prefix elements are inserted at the block 14 marker to preserve CSS order.
- The two prefix document.readyState checks treat the period before
  DOMContentLoaded as loading. This keeps their original event registration.
- Catalog startup uses the original home-page paint scheduling and immediate
  category/product startup after DOMContentLoaded.
- The primary URL is pinned to a Git commit on jsDelivr. The HTML includes SRI.
- A load error fetches the same pinned bytes from raw.githubusercontent.com,
  with the same integrity check. Fallback waits until document parsing ends.
- g.done prevents duplicate initialization if both sources become available.

Maintenance: the small Tilda blocks are intentional loaders. Download the
version-pinned runtime referenced in block 13 when examining the actual code.
Changing a file on main does not change the pinned production version. Update
the commit URL and integrity value together when shipping a changed runtime.
Keep blocks 13 and 14 paired. Do not alter the independent bath blocks while
updating this runtime.

Rollback: restore the complete previous inline blocks 13 v308P3 and 14 v99P1,
then republish only the homepage. No data or external assets need deleting.
