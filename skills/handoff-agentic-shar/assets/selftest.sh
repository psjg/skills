set -e
rm -rf .selftest && mkdir -p .selftest/awk .selftest/py .selftest/emacs
for d in awk py emacs; do
  sed -e 's|__TITLE__|t|g' -e 's|__AUTHOR__|a|g' -e 's|__DATE__|d|g' -e 's|__LANG__|sh|g' \
      -e 's|__FIRST_FILE__|hello.sh|g' -e 's|__PACKAGES__||g' -e 's|__CHECK__|sh hello.sh|g' \
      -e 's|__FIRST_CHAPTER__|Hello|g' -e 's|__FIRST_CODE__|echo hello|g' -e 's|__SEAMS__|seam-example|g' \
      assets/template.org > .selftest/$d/SHAR.org
done
(cd .selftest/awk && sh SHAR.org tangle)
(cd .selftest/py && python3 ../../assets/tangle.py SHAR.org)
(cd .selftest/emacs && emacs --batch -l org --eval '(org-babel-tangle-file "SHAR.org")' >/dev/null 2>&1)
diff -r .selftest/awk .selftest/py && diff -r .selftest/awk .selftest/emacs && rm -rf .selftest && echo SELFTEST-OK
