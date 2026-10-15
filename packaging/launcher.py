"""Entry point of the packaged BookReader.exe."""
import sys

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        from bookreader.selftest import main as selftest
        i = sys.argv.index("--selftest")
        sys.exit(selftest(sys.argv[i + 1] if i + 1 < len(sys.argv) else "selftest.txt"))
    from bookreader.gui import main
    sys.exit(main())
