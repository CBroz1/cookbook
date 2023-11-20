#!/bin/bash

run_replace=true
run_fracts=true
run_units=false

declare -A replacements=(
  [" cup"]="c"
  [" tablespoon"]="tbsp"
  [" teaspoon"]="tsp"
  [" ounce"]="oz"
  [" minute"]="min"
  [" pounds"]="lbs"
  [" cloves of garlic"]=" garlic"
)

declare -A fractions=(
  [" ½"]=".5"
  [" ⅓"]=".33"
  [" ¼"]=".25"
  [" ⅛"]=".125"
  [" ¾"]=".75"
  ["½"]=".5"
  ["⅓"]=".33"
  ["¼"]=".25"
  ["⅛"]=".125"
  ["¾"]=".75"
  ["1/2"]="0.5"   # Add multi-character fractions here
  ["1/3"]="0.3333"
  ["1/4"]="0.25"
  ["1/8"]="0.125"
)


# Ensure provided file string
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <filename>"
  exit 1
fi

filename="$1"

# Use fzf to find the file if it doesn't exist
if [ ! -e "$filename" ]; then
  selected_file=$(fzf --query="$filename" --preview="ls -l {}")
  if [ -z "$selected_file" ]; then
    echo "No file selected. Exiting."
    exit 1
  fi
  filename="$selected_file"
fi

# Replace units with abbreviations
if [ "$run_replace" = true ]; then
  for target in "${!replacements[@]}"; do
    replacement="${replacements[$target]}"
    sed -i "s/\b${target}\(\(s\)\?\|\b\)/${replacement}/g" "$filename"
  done
fi

# Replace fractions with decimal equivalents
if [ "$run_fracts" = true ]; then
  for fraction in "${!fractions[@]}"; do
    decimal_equivalent="${fractions[$fraction]}"
    sed -i "s#${fraction}#${decimal_equivalent}#g" "$filename"
  done
fi


# Replace unit prefix with suffix in lists # NOT WORKING
if [ "$run_units" = true ]; then
  sed -i -E '/^(\s*-\s*|\s*[1-9]+\.?\d*\s*)/ {
    s/([0-9]+\.?[0-9]*)([a-z]+)/\2 (\1)/g
  }' "$filename" && awk '/^(\s*-\s*|\s*[1-9]+\.?\d*\s*)/{gsub(/([0-9]+\.?[0-9]*)([a-z]+)/, "\t- \\2 (\\1)"); print} !/^\s*-|^[\t\s]*[1-9]+\./ {print}' "$filename"
fi
