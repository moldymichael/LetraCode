Name:           letracode
Version:        0.3.0
Release:        1%{?dist}
Summary:        Private local AI conversations with project context
License:        MIT
Source0:        LetraCode-%{version}.tar.gz
BuildArch:      noarch

Requires:       python3 >= 3.11
Requires:       python3-pyside6
Requires:       python3-pypdf

%description
LetraCode is a KDE-friendly desktop chat application that connects to a local,
managed llama.cpp server. Conversations remain on the user's computer.

%prep
%autosetup -n LetraCode-%{version}

%build

%install
mkdir -p %{buildroot}%{_datadir}/letracode
cp -a letracode %{buildroot}%{_datadir}/letracode/
install -Dm0644 LICENSE %{buildroot}%{_licensedir}/%{name}/LICENSE
install -Dm0644 README.md %{buildroot}%{_docdir}/%{name}/README.md

install -d %{buildroot}%{_bindir}
cat > %{buildroot}%{_bindir}/letracode <<'EOF'
#!/bin/sh
export PYTHONPATH="%{_datadir}/letracode${PYTHONPATH:+:$PYTHONPATH}"
exec %{_bindir}/python3 -P -m letracode "$@"
EOF
chmod 0755 %{buildroot}%{_bindir}/letracode

install -Dm0644 packaging/io.letracode.LetraCode.desktop \
    %{buildroot}%{_datadir}/applications/io.letracode.LetraCode.desktop
install -Dm0644 letracode/assets/io.letracode.LetraCode.svg \
    %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/io.letracode.LetraCode.svg
install -Dm0644 packaging/io.letracode.LetraCode.metainfo.xml \
    %{buildroot}%{_metainfodir}/io.letracode.LetraCode.metainfo.xml

%check
PYTHONPATH=%{buildroot}%{_datadir}/letracode %{_bindir}/python3 -P -m letracode --version

%files
%license %{_licensedir}/%{name}/LICENSE
%doc %{_docdir}/%{name}/README.md
%{_bindir}/letracode
%{_datadir}/letracode/
%{_datadir}/applications/io.letracode.LetraCode.desktop
%{_datadir}/icons/hicolor/scalable/apps/io.letracode.LetraCode.svg
%{_metainfodir}/io.letracode.LetraCode.metainfo.xml

%changelog
* Tue Sep 08 2026 LetraCode contributors - 0.3.0-1
- Native Windows support while preserving Fedora and project recovery behavior

* Tue Sep 08 2026 LetraCode contributors - 0.2.1-1
- File-first project interface with existing Memory recovery support

* Sat Sep 05 2026 LetraCode contributors - 0.1.1-1
- Remove unavailable python3-docx dependency; include bounded DOCX text reader

* Sat Sep 05 2026 LetraCode contributors - 0.1.0-1
- Initial package
