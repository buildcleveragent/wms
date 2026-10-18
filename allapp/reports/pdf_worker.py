"""Isolated Linux PDF renderer; never load Django or database credentials."""

import sys


def main():
    import resource

    output, memory, cpu, max_output = sys.argv[1:]
    resource.setrlimit(resource.RLIMIT_AS, (int(memory), int(memory)))
    resource.setrlimit(resource.RLIMIT_CPU, (int(cpu), int(cpu)))
    resource.setrlimit(resource.RLIMIT_FSIZE, (int(max_output), int(max_output)))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    from pdf_fetcher import OfflineURLFetcher
    from weasyprint import HTML

    HTML(string=sys.stdin.buffer.read().decode("utf-8"), url_fetcher=OfflineURLFetcher()).write_pdf(
        output, presentational_hints=False
    )


if __name__ == "__main__":
    main()
