get_available_cores <- function(max_cores = NULL, reserve_cores = 1L) {
  physical <- suppressWarnings(parallel::detectCores(logical = FALSE))
  logical <- suppressWarnings(parallel::detectCores(logical = TRUE))
  detected <- physical
  if (is.na(detected) || detected < 1L) {
    detected <- logical
  }
  if (is.na(detected) || detected < 1L) {
    detected <- 1L
  }

  usable <- max(1L, as.integer(detected) - as.integer(reserve_cores))
  if (!is.null(max_cores) && !is.na(max_cores)) {
    usable <- min(usable, as.integer(max_cores))
  }
  usable
}

resolve_cores <- function(requested_cores = NULL, n_tasks = NULL, max_cores = NULL, reserve_cores = 1L) {
  cores <- requested_cores
  if (is.null(cores) || is.na(cores)) {
    cores <- get_available_cores(max_cores = max_cores, reserve_cores = reserve_cores)
  }
  cores <- max(1L, as.integer(cores))
  if (!is.null(n_tasks) && !is.na(n_tasks)) {
    cores <- min(cores, max(1L, as.integer(n_tasks)))
  }
  cores
}

parallel_map <- function(items, fun, ..., cores = NULL, max_cores = NULL, reserve_cores = 1L) {
  if (length(items) == 0) {
    return(list())
  }

  use_cores <- resolve_cores(
    requested_cores = cores,
    n_tasks = length(items),
    max_cores = max_cores,
    reserve_cores = reserve_cores
  )

  if (.Platform$OS.type == "unix" && use_cores > 1L) {
    return(parallel::mclapply(items, fun, ..., mc.cores = use_cores))
  }

  lapply(items, fun, ...)
}

fast_read_csv <- function(csv_path) {
  if (requireNamespace("data.table", quietly = TRUE)) {
    return(data.table::fread(
      csv_path,
      showProgress = FALSE,
      data.table = FALSE
    ))
  }

  utils::read.csv(csv_path, stringsAsFactors = FALSE)
}
