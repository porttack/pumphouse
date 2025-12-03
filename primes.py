#!/usr/bin/env python3
"""
Prime number generator program.
Generates prime numbers using the Sieve of Eratosthenes algorithm.
"""

import sys
import argparse


def sieve_of_eratosthenes(limit):
    """
    Generate all prime numbers up to the given limit using the Sieve of Eratosthenes.
    
    Args:
        limit: The upper bound (inclusive) for generating primes
        
    Returns:
        List of prime numbers up to and including limit
    """
    if limit < 2:
        return []
    
    # Create a boolean array "is_prime[0..limit]" and initialize all entries as true
    is_prime = [True] * (limit + 1)
    is_prime[0] = is_prime[1] = False  # 0 and 1 are not prime
    
    p = 2
    while p * p <= limit:
        # If is_prime[p] is not changed, then it is a prime
        if is_prime[p]:
            # Update all multiples of p
            for i in range(p * p, limit + 1, p):
                is_prime[i] = False
        p += 1
    
    # Collect all numbers that are still marked as prime
    primes = [num for num in range(2, limit + 1) if is_prime[num]]
    return primes


def generate_n_primes(n):
    """
    Generate the first n prime numbers.
    
    Args:
        n: Number of primes to generate
        
    Returns:
        List of the first n prime numbers
    """
    if n <= 0:
        return []
    
    # Start with a reasonable estimate for the upper limit
    # Using the prime number theorem approximation: n * ln(n) + n * ln(ln(n))
    if n < 6:
        limit = 15
    else:
        import math
        limit = int(n * (math.log(n) + math.log(math.log(n)))) + 100
    
    # Generate primes and increase limit if needed
    while True:
        primes = sieve_of_eratosthenes(limit)
        if len(primes) >= n:
            return primes[:n]
        limit *= 2


def is_prime(num):
    """
    Check if a number is prime.
    
    Args:
        num: Number to check
        
    Returns:
        True if num is prime, False otherwise
    """
    if num < 2:
        return False
    if num == 2:
        return True
    if num % 2 == 0:
        return False
    
    # Check odd divisors up to sqrt(num)
    i = 3
    while i * i <= num:
        if num % i == 0:
            return False
        i += 2
    
    return True


def main():
    """Main entry point for the prime generator program"""
    parser = argparse.ArgumentParser(
        description='Generate prime numbers',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --limit 100         Generate all primes up to 100
  %(prog)s --count 20          Generate the first 20 primes
  %(prog)s --check 17          Check if 17 is prime
  %(prog)s --limit 50 --csv    Output in CSV format
        """
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--limit', type=int, metavar='N',
                      help='Generate all primes up to N')
    group.add_argument('--count', type=int, metavar='N',
                      help='Generate the first N prime numbers')
    group.add_argument('--check', type=int, metavar='N',
                      help='Check if N is a prime number')
    
    parser.add_argument('--csv', action='store_true',
                       help='Output in CSV format (comma-separated)')
    parser.add_argument('--quiet', action='store_true',
                       help='Only output the numbers, no headers')
    
    args = parser.parse_args()
    
    try:
        if args.check is not None:
            # Check if a number is prime
            result = is_prime(args.check)
            if not args.quiet:
                if result:
                    print(f"{args.check} is prime")
                else:
                    print(f"{args.check} is not prime")
            else:
                print("yes" if result else "no")
        
        elif args.limit is not None:
            # Generate primes up to limit
            primes = sieve_of_eratosthenes(args.limit)
            
            if not args.quiet:
                print(f"Prime numbers up to {args.limit}:")
                print(f"Found {len(primes)} primes")
                print()
            
            if args.csv:
                print(','.join(map(str, primes)))
            else:
                for prime in primes:
                    print(prime)
        
        elif args.count is not None:
            # Generate first n primes
            primes = generate_n_primes(args.count)
            
            if not args.quiet:
                print(f"First {args.count} prime numbers:")
                print()
            
            if args.csv:
                print(','.join(map(str, primes)))
            else:
                for prime in primes:
                    print(prime)
    
    except KeyboardInterrupt:
        print("\n\nInterrupted by user", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
