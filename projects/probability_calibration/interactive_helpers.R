prompt_text_value <- function(prompt, default = NULL, allow_empty = FALSE) {
  repeat {
    suffix <- if (!is.null(default) && nzchar(default)) sprintf(" [%s]", default) else ""
    answer <- trimws(readline(paste0(prompt, suffix, ": ")))
    if (nzchar(answer)) {
      return(answer)
    }
    if (!is.null(default)) {
      return(default)
    }
    if (allow_empty) {
      return("")
    }
    cat("请输入一个值。\n")
  }
}

prompt_integer_value <- function(prompt, default = NULL, min_value = NULL) {
  default_text <- if (is.null(default)) NULL else as.character(default)

  repeat {
    answer <- prompt_text_value(prompt, default = default_text)
    value <- suppressWarnings(as.integer(answer))
    if (!is.na(value) && (is.null(min_value) || value >= min_value)) {
      return(value)
    }

    if (is.null(min_value)) {
      cat("请输入一个整数。\n")
    } else {
      cat(sprintf("请输入一个大于等于 %d 的整数。\n", min_value))
    }
  }
}

prompt_choice_value <- function(prompt, choices, default = choices[1]) {
  options_text <- paste(sprintf("%d=%s", seq_along(choices), choices), collapse = ", ")

  repeat {
    answer <- prompt_text_value(
      sprintf("%s (%s)", prompt, options_text),
      default = default
    )
    if (answer %in% choices) {
      return(answer)
    }

    index <- suppressWarnings(as.integer(answer))
    if (!is.na(index) && index >= 1 && index <= length(choices)) {
      return(choices[index])
    }

    cat("请输入选项编号或选项文字。\n")
  }
}

prompt_yes_no_value <- function(prompt, default = TRUE) {
  default_text <- if (isTRUE(default)) "y" else "n"

  repeat {
    answer <- tolower(prompt_text_value(
      sprintf("%s (y/n)", prompt),
      default = default_text
    ))
    if (answer %in% c("y", "yes", "1")) {
      return(TRUE)
    }
    if (answer %in% c("n", "no", "0")) {
      return(FALSE)
    }
    cat("请输入 y 或 n。\n")
  }
}

prompt_path_value <- function(prompt, default = NULL) {
  chosen <- prompt_text_value(prompt, default = default)
  normalizePath(chosen, winslash = "/", mustWork = FALSE)
}

prompt_optional_path <- function(prompt, default = NULL) {
  hints <- "回车跳过"
  if (!is.null(default) && nzchar(default)) {
    hints <- "回车使用默认路径；输入 skip 跳过"
  }

  answer <- trimws(readline(paste0(prompt, "（", hints, "）: ")))
  if (!nzchar(answer)) {
    if (!is.null(default) && nzchar(default)) {
      return(normalizePath(default, winslash = "/", mustWork = FALSE))
    }
    return(NULL)
  }

  if (tolower(answer) %in% c("skip", "none", "no")) {
    return(NULL)
  }

  normalizePath(answer, winslash = "/", mustWork = FALSE)
}

announce_interactive_ready <- function(script_label, fn_name = "run_interactive", eval_env = parent.frame()) {
  if (interactive() && identical(eval_env, globalenv())) {
    cat(sprintf("[%s] 已加载。运行 %s() 按提示交互输入，或直接调用 main(...)\n", script_label, fn_name))
  }
}
