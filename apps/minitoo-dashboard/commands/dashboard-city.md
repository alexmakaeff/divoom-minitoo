---
description: Set the city for the MiniToo dashboard weather page
argument-hint: <city name>
---
Set the MiniToo dashboard weather city to: $ARGUMENTS

Run `"__BIN__" city $ARGUMENTS`. If it prints a numbered list and asks for `--pick`, show the list to the user, ask which place they mean, then run `"__BIN__" city $ARGUMENTS --pick <number>`. Report the city that was saved.
