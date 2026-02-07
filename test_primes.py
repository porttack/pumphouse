#!/usr/bin/env python3
"""
Test script for prime number generator.
Validates the correctness of prime generation algorithms.
"""

import sys
import os

# Add current directory to path to import primes module
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from primes import sieve_of_eratosthenes, generate_n_primes, is_prime


def test_is_prime():
    """Test the is_prime function"""
    print("Testing is_prime function...")
    
    # Test known primes
    primes = [
        2, 3, 5, 7, 11, 13, 17, 19, 23, 29,
        31, 37, 41, 43, 47, 53, 59, 61, 67, 71,
        73, 79, 83, 89, 97
    ]
    for p in primes:
        assert is_prime(p), f"Failed: {p} should be prime"
    
    # Test known non-primes
    non_primes = [
        0, 1, 4, 6, 8, 9, 10, 12, 14, 15,
        16, 18, 20, 21, 22, 24, 25, 26, 27, 28, 30
    ]
    for n in non_primes:
        assert not is_prime(n), f"Failed: {n} should not be prime"
    
    # Test edge cases
    assert not is_prime(-5), "Negative numbers should not be prime"
    assert not is_prime(-1), "-1 should not be prime"
    
    # Test larger primes
    assert is_prime(101), "101 should be prime"
    assert is_prime(1009), "1009 should be prime"
    assert not is_prime(1000), "1000 should not be prime"
    
    print("✓ is_prime tests passed")


def test_sieve_of_eratosthenes():
    """Test the sieve of eratosthenes function"""
    print("Testing sieve_of_eratosthenes function...")
    
    # Test small limits
    assert sieve_of_eratosthenes(1) == [], "No primes below 2"
    assert sieve_of_eratosthenes(2) == [2], "Only 2 for limit 2"
    assert sieve_of_eratosthenes(10) == [2, 3, 5, 7], "Primes up to 10"
    
    # Test known sequence
    primes_30 = sieve_of_eratosthenes(30)
    expected_30 = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    assert primes_30 == expected_30, f"Expected {expected_30}, got {primes_30}"
    
    # Test that result is sorted
    primes_100 = sieve_of_eratosthenes(100)
    assert primes_100 == sorted(primes_100), "Primes should be sorted"
    
    # Test count matches expected
    assert len(primes_100) == 25, "There are 25 primes up to 100"
    
    # Verify all returned numbers are actually prime
    for p in primes_100:
        assert is_prime(p), f"{p} in result but is not prime"
    
    print("✓ sieve_of_eratosthenes tests passed")


def test_generate_n_primes():
    """Test the generate_n_primes function"""
    print("Testing generate_n_primes function...")
    
    # Test small counts
    assert generate_n_primes(0) == [], "0 primes should return empty list"
    assert generate_n_primes(1) == [2], "First prime is 2"
    assert generate_n_primes(5) == [2, 3, 5, 7, 11], "First 5 primes"
    
    # Test known sequence
    primes_10 = generate_n_primes(10)
    expected_10 = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29]
    assert primes_10 == expected_10, f"Expected {expected_10}, got {primes_10}"
    
    # Test count is exact
    for n in [1, 5, 10, 20, 50]:
        result = generate_n_primes(n)
        assert len(result) == n, f"Expected {n} primes, got {len(result)}"
    
    # Verify all returned numbers are actually prime
    primes_50 = generate_n_primes(50)
    for p in primes_50:
        assert is_prime(p), f"{p} in result but is not prime"
    
    # Test that result is sorted
    assert primes_50 == sorted(primes_50), "Primes should be sorted"
    
    print("✓ generate_n_primes tests passed")


def test_consistency():
    """Test that both methods give consistent results"""
    print("Testing consistency between methods...")
    
    # Generate first 20 primes using both methods
    count_method = generate_n_primes(20)
    
    # Find the largest prime from count method
    max_prime = count_method[-1]
    
    # Generate all primes up to that number
    sieve_method = sieve_of_eratosthenes(max_prime)
    
    # They should match
    assert count_method == sieve_method, "Methods should produce same results"
    
    print("✓ Consistency tests passed")


def test_edge_cases():
    """Test edge cases and boundary conditions"""
    print("Testing edge cases...")
    
    # Negative inputs
    assert generate_n_primes(-1) == [], "Negative count should return empty list"
    assert sieve_of_eratosthenes(-10) == [], "Negative limit should return empty list"
    
    # Zero inputs
    assert generate_n_primes(0) == [], "Zero count should return empty list"
    assert sieve_of_eratosthenes(0) == [], "Zero limit should return empty list"
    
    # Boundary of 2 (first prime)
    assert is_prime(2), "2 should be prime"
    assert sieve_of_eratosthenes(2) == [2], "Sieve at 2 should return [2]"
    
    print("✓ Edge case tests passed")


def run_all_tests():
    """Run all test functions"""
    print("=" * 50)
    print("Running Prime Generator Tests")
    print("=" * 50)
    print()
    
    try:
        test_is_prime()
        print()
        test_sieve_of_eratosthenes()
        print()
        test_generate_n_primes()
        print()
        test_consistency()
        print()
        test_edge_cases()
        print()
        print("=" * 50)
        print("✓ All tests passed successfully!")
        print("=" * 50)
        return 0
    
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(run_all_tests())
