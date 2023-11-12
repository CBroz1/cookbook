#!/bin/bash

# Check if a file is provided as an argument
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 <filename>"
  exit 1
fi

filename="$1"

# Check if the file exists in the current directory
if [ ! -e "$filename" ]; then
  # If the file doesn't exist, use fzf to find the closest match
  selected_file=$(fzf --query="$filename" --preview="ls -l {}")
  
  # Check if a file was selected
  if [ -z "$selected_file" ]; then
    echo "No file selected. Exiting."
    exit 1
  fi
  
  filename="$selected_file"
fi

# Define an associative array
declare -A replacements=(
  [" cup"]="c"
  [" tablespoon"]="tbsp"
  [" teaspoon"]="tsp"
  [" ounce"]="oz"
  [" minute"]="min"
)

# Perform find/replace operations using sed
for target in "${!replacements[@]}"; do
  replacement="${replacements[$target]}"
  sed -i "s/\b${target}\(\(s\)\?\|\b\)/${replacement}/g" "$filename"
done

# Define an associative array of fractions and decimal equivalents
declare -A fractions=(
  ["½"]=".5"
  ["⅓"]=".33"
  ["¼"]=".25"
  ["⅛"]=".125"
)

# Perform find/replace operations for single-character fractions
for fraction in "${!fractions[@]}"; do
  decimal_equivalent="${fractions[$fraction]}"
  sed -i "s/${fraction}/${decimal_equivalent}/g" "$filename"
done

