=================
Media Monkey remarks
=================

-----------------
Ratings
-----------------
Media Monkey stores it's ratings in the *POPM* ID3 tag.
The email used to store them is ``no@email``.
The ratings range from 0 stars to 5 stars in half-star steps (0.0, 0.5, 1.0, ...).
The ratings are represented in the ID3 tag with an integer in the range [0, 255].
However the star-based ratings to not translate to a scaled version in the ID3 tag range.
The exact mapping is:

=====	=========
Stars	ID3 Value
=====	=========
0.0		0
0.5		13
1.0		1
1.5		54
2.0		64
2.5		118
3.0		128
3.5		186
4.0		196
4.5		242
5.0		255
