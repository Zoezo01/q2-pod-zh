Q2 Pod Classic Light: a modified version of TeX Gyre Heros Bold
======================================================

fonts/classic-light.ttf in a Q2 Pod firmware image is a Modified Version of TeX Gyre Heros Bold
(version 2.004), made from texgyreheros-bold.otf in this folder by tools/build.py (subset_font):

- cut to what it has of U+0020-U+052F and U+2000-U+21FF: part of its Latin and Greek and its punctuation;
- its layout features cut to frac, liga and locl;
- renamed Q2 Pod Classic Light, in the name table and the CFF names, as GUST asks of derived fonts. Its
  copyright and trademark notices (name IDs 0 and 7) are kept, and its description (name ID 10)
  says it is modified and where the original is.

No outline or metric is changed. texgyreheros-bold.otf in this folder is the unmodified original.
The Q2 Pod project maintains this version: the authors of TeX Gyre Heros and GUST do not support it,
and questions about it go to Q2 Pod, not to them.

The original, with the whole TeX Gyre Heros family and its manifest:
https://www.gust.org.pl/projects/e-foundry/tex-gyre/heros
Copyright (c) 2006, 2009 B. Jackowski and J. M. Nowacki (on behalf of TeX Users Groups)

Both are under the GUST Font License, GUST-FONT-LICENSE.txt in this folder: the LaTeX Project
Public License 1.3c or later (https://www.latex-project.org/lppl/) with GUST's request about names.
The rest of this repository is under its MIT LICENSE, which does not cover these fonts.
