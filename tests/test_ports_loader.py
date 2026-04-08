"""Tests for ports_loader module."""

from ports_loader import PortsLoader, PORTS


class TestPortsLoaderSearch:
    def setup_method(self):
        self.loader = PortsLoader()

    def test_search_prefix_match(self):
        """query='Rot' -> Rotterdam should be in results."""
        results = self.loader.search("Rot")
        names = [p["name"] for p in results]
        assert "Rotterdam" in names

    def test_search_contains_match(self):
        """query='dam' -> Rotterdam should appear (contains 'dam')."""
        results = self.loader.search("dam")
        names = [p["name"] for p in results]
        assert "Rotterdam" in names

    def test_search_empty_string(self):
        """query='' -> returns empty list."""
        results = self.loader.search("")
        assert results == []


class TestPortsLoaderGet:
    def setup_method(self):
        self.loader = PortsLoader()

    def test_get_by_id(self):
        """get('nlrtm') -> returns dict with name='Rotterdam'."""
        port = self.loader.get("nlrtm")
        assert port is not None
        assert port["name"] == "Rotterdam"

    def test_get_by_locode(self):
        """get('NLRTM') -> returns dict with name='Rotterdam'."""
        port = self.loader.get("NLRTM")
        assert port is not None
        assert port["name"] == "Rotterdam"

    def test_get_not_found(self):
        """get('XXXXX') -> returns None."""
        port = self.loader.get("XXXXX")
        assert port is None


class TestPortsData:
    def test_all_ports_have_required_fields(self):
        """Every port in PORTS must have id, name, lat, lon, locode, country."""
        required = {"id", "name", "lat", "lon", "locode", "country"}
        for port in PORTS:
            missing = required - port.keys()
            assert not missing, f"Port {port.get('name', '?')} missing fields: {missing}"

    def test_unique_locodes(self):
        """Verify no duplicate locodes exist (after the BRREC fix)."""
        locodes = [p["locode"] for p in PORTS]
        duplicates = [lc for lc in locodes if locodes.count(lc) > 1]
        assert not duplicates, f"Duplicate locodes found: {set(duplicates)}"
